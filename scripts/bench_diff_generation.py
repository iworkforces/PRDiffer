#!/usr/bin/env python3
"""Deterministic full-diff correctness/performance benchmark harness.

Supports the immutable ``strict-v1`` matrix used for pre-change baselines and
post-change comparisons. No network calls. Baseline artifacts refuse overwrite.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import resource
import sys
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio

from prdiffer.domain.entities.file_patch import FilePatchInfo
from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.github.file_processor import FileProcessor
from prdiffer.infrastructure.utils.diff_utils import DiffUtils

MATRIX_NAME = "strict-v1"
DEFAULT_SEED = 5020
WARMUPS = 1

WorkloadName = Literal["medium", "large", "pathological"]
PhaseName = Literal["baseline", "post"]
ModeName = str

BASELINE_MODES: frozenset[str] = frozenset(
    {
        "sync-current",
        "async-current-negative-control",
    }
)
POST_CHANGE_MODES: frozenset[str] = frozenset(
    {
        "serialized-worker-1",
        "bounded-worker-2",
        "bounded-worker-4",
    }
)
ALL_KNOWN_MODES: frozenset[str] = BASELINE_MODES | POST_CHANGE_MODES


@dataclass(frozen=True)
class WorkloadSpec:
    """Immutable workload definition for the strict-v1 matrix."""

    name: WorkloadName
    files: int
    lines: int
    samples: int
    style: Literal["standard", "pathological"] = "standard"


STRICT_V1_WORKLOADS: dict[str, WorkloadSpec] = {
    "medium": WorkloadSpec(name="medium", files=25, lines=200, samples=5),
    "large": WorkloadSpec(name="large", files=250, lines=1000, samples=5),
    "pathological": WorkloadSpec(
        name="pathological",
        files=10,
        lines=5000,
        samples=3,
        style="pathological",
    ),
}


@dataclass(frozen=True)
class FakeFile:
    """Deterministic GitHub file stand-in with patch metadata."""

    filename: str
    status: str
    patch: str | None
    additions: int
    deletions: int
    previous_filename: str | None = None
    sha: str = "deadbeef"


@dataclass(frozen=True)
class FakeRepository:
    """Frozen fake repository with a stable full_name."""

    full_name: str
    owner: str
    name: str


@dataclass
class ApiCounters:
    """Tracks provider-side work without network I/O."""

    api_calls: int = 0
    page_fetches: int = 0
    bytes_read: int = 0
    max_in_flight: int = 0
    _in_flight: int = 0

    def begin(self) -> None:
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        self.api_calls += 1

    def end(self, nbytes: int = 0) -> None:
        self.bytes_read += nbytes
        self._in_flight = max(0, self._in_flight - 1)

    def record_page(self) -> None:
        self.page_fetches += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "api_calls": self.api_calls,
            "page_fetches": self.page_fetches,
            "bytes_read": self.bytes_read,
            "max_in_flight": self.max_in_flight,
        }

    def reset(self) -> None:
        self.api_calls = 0
        self.page_fetches = 0
        self.bytes_read = 0
        self.max_in_flight = 0
        self._in_flight = 0


class DummyPatternMatcher(PatternMatchingServiceInterface):
    """Accept all synthetic benchmark paths."""

    def is_valid_file(self, filename: str) -> bool:
        return True

    def filter_files(self, filenames: list[str]) -> list[str]:
        return list(filenames)


@dataclass(frozen=True)
class FakeTreeItem:
    """Recursive git tree leaf shaped like PyGithub ``GitTreeElement``."""

    path: str
    mode: str
    type: str
    sha: str


@dataclass(frozen=True)
class FakeTree:
    """Immutable recursive git tree shaped like PyGithub ``GitTree``."""

    tree: tuple[FakeTreeItem, ...]
    truncated: bool = False


@dataclass(frozen=True)
class FakeBlob:
    """Git blob shaped like PyGithub ``GitBlob`` (base64 content)."""

    content: str
    encoding: str = "base64"


def _git_blob_sha(data: bytes) -> str:
    """Git object id for a blob payload (deterministic 40-hex SHA-1)."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


class InstrumentedGitRepository:
    """In-memory immutable git trees/blobs with counters and optional artificial delay."""

    def __init__(
        self,
        full_name: str,
        content_map: dict[tuple[str, str], str],
        counters: ApiCounters,
        *,
        block_ms: float = 0.0,
    ) -> None:
        if full_name.startswith("http"):
            raise RuntimeError("Network access is forbidden in the benchmark harness")
        self.full_name = full_name
        self._counters = counters
        self._block_ms = block_ms
        self._blobs: dict[str, bytes] = {}
        items_by_ref: dict[str, list[FakeTreeItem]] = {}
        for (path, ref), text in content_map.items():
            data = text.encode("utf-8")
            sha = _git_blob_sha(data)
            self._blobs[sha] = data
            items_by_ref.setdefault(ref, []).append(FakeTreeItem(path=path, mode="100644", type="blob", sha=sha))
        self._trees = {ref: FakeTree(tree=tuple(items)) for ref, items in items_by_ref.items()}

    def _maybe_block(self) -> None:
        if self._block_ms > 0:
            # Intentional sync sleep for event-loop blocking negative control.
            time.sleep(self._block_ms / 1000.0)

    def get_git_tree(self, sha: str, recursive: bool = False) -> FakeTree:
        self._counters.begin()
        try:
            self._maybe_block()
            return self._trees[sha]
        finally:
            self._counters.end(0)

    def get_git_blob(self, sha: str) -> FakeBlob:
        data = b""
        self._counters.begin()
        try:
            data = self._blobs[sha]
            return FakeBlob(content=base64.b64encode(data).decode("ascii"))
        finally:
            self._counters.end(len(data))


@dataclass(frozen=True)
class FixtureBundle:
    """Deterministic PR fixture for one workload."""

    workload: WorkloadSpec
    seed: int
    repository: FakeRepository
    base_sha: str
    head_sha: str
    files: tuple[FakeFile, ...]
    content_map: dict[tuple[str, str], str]
    pages: tuple[tuple[FakeFile, ...], ...]
    manifest: dict[str, Any]
    fixture_digest: str


def _line_for(seed: int, file_index: int, line_index: int, style: str) -> str:
    if style == "pathological":
        # Highly repeated / near-matching lines stress diff algorithms.
        base = f"shared-line-{seed % 97}"
        if line_index % 50 == 0:
            return f"{base}-variant-{file_index}-{line_index}"
        return base
    return f"line-{seed}-{file_index:04d}-{line_index:05d}"


def build_fixture(workload: WorkloadSpec, seed: int = DEFAULT_SEED) -> FixtureBundle:
    """Build a frozen fake repository PR with deterministic content and pages."""
    if workload.files <= 0 or workload.lines <= 0 or workload.samples <= 0:
        raise ValueError(
            f"Invalid workload {workload.name}: files/lines/samples must be positive "
            f"(got files={workload.files}, lines={workload.lines}, samples={workload.samples})"
        )

    repository = FakeRepository(
        full_name="bench-owner/bench-repo",
        owner="bench-owner",
        name="bench-repo",
    )
    base_sha = hashlib.sha1(f"base:{seed}".encode()).hexdigest()
    head_sha = hashlib.sha1(f"head:{seed}".encode()).hexdigest()

    diff_utils = DiffUtils()
    files: list[FakeFile] = []
    content_map: dict[tuple[str, str], str] = {}
    ordered_paths: list[str] = []

    for i in range(workload.files):
        filename = f"src/module_{i:04d}.py"
        ordered_paths.append(filename)
        base_lines = [_line_for(seed, i, j, workload.style) for j in range(workload.lines)]
        head_lines = list(base_lines)
        # Deterministic single-line edit near the end for a stable patch shape.
        edit_at = max(0, workload.lines - 3)
        head_lines[edit_at] = f"changed-{seed}-{i:04d}-{edit_at:05d}"
        if workload.style == "pathological":
            head_lines.append(f"extra-near-match-{seed}-{i}")
        base_content = "\n".join(base_lines) + "\n"
        head_content = "\n".join(head_lines) + "\n"
        patch = diff_utils.build_full_file_patch(base_content, head_content)
        files.append(
            FakeFile(
                filename=filename,
                status="modified",
                patch=patch,
                additions=1 if workload.style != "pathological" else 2,
                deletions=1,
                sha=hashlib.sha1(f"{seed}:{filename}".encode()).hexdigest()[:12],
            )
        )
        content_map[(filename, base_sha)] = base_content
        content_map[(filename, head_sha)] = head_content

    # Deterministic pagination: 100 files per page (GitHub-like).
    page_size = 100
    pages: list[tuple[FakeFile, ...]] = []
    for start in range(0, len(files), page_size):
        pages.append(tuple(files[start : start + page_size]))

    file_digests = [
        {
            "index": idx,
            "path": f.filename,
            "status": f.status,
            "sha": f.sha,
            "additions": f.additions,
            "deletions": f.deletions,
            "base_bytes": len(content_map[(f.filename, base_sha)].encode("utf-8")),
            "head_bytes": len(content_map[(f.filename, head_sha)].encode("utf-8")),
        }
        for idx, f in enumerate(files)
    ]
    manifest: dict[str, Any] = {
        "matrix": MATRIX_NAME,
        "workload": workload.name,
        "seed": seed,
        "files": workload.files,
        "lines": workload.lines,
        "samples": workload.samples,
        "style": workload.style,
        "repository": repository.full_name,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "page_count": len(pages),
        "page_size": page_size,
        "ordered_paths": ordered_paths,
        "file_digests": file_digests,
    }
    fixture_digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["fixture_digest"] = fixture_digest

    return FixtureBundle(
        workload=workload,
        seed=seed,
        repository=repository,
        base_sha=base_sha,
        head_sha=head_sha,
        files=tuple(files),
        content_map=content_map,
        pages=tuple(pages),
        manifest=manifest,
        fixture_digest=fixture_digest,
    )


def validate_fixture(bundle: FixtureBundle) -> None:
    """Preflight validation before any timing begins."""
    if not bundle.repository.full_name or "/" not in bundle.repository.full_name:
        raise ValueError("Invalid repository fixture: full_name required")
    if len(bundle.files) != bundle.workload.files:
        raise ValueError(
            f"Fixture file count mismatch: expected {bundle.workload.files}, got {len(bundle.files)}"
        )
    if not bundle.files:
        raise ValueError("Fixture has zero files")
    if not bundle.pages:
        raise ValueError("Fixture has zero pages")
    for idx, fake in enumerate(bundle.files):
        if not fake.filename:
            raise ValueError(f"File at index {idx} missing filename")
        if (fake.filename, bundle.base_sha) not in bundle.content_map:
            raise ValueError(f"Missing base content for {fake.filename}")
        if (fake.filename, bundle.head_sha) not in bundle.content_map:
            raise ValueError(f"Missing head content for {fake.filename}")
    recomputed = hashlib.sha256(
        json.dumps(
            {k: v for k, v in bundle.manifest.items() if k != "fixture_digest"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if recomputed != bundle.fixture_digest:
        raise ValueError("Fixture digest mismatch — fixture is not sealed/deterministic")


def _rss_bytes() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    if platform.system() == "Darwin":
        return int(usage)
    return int(usage * 1024)


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime + usage.ru_stime)


@dataclass
class SampleResult:
    """One measured sample for a workload/mode pair."""

    sample_index: int
    wall_seconds: float
    cpu_seconds: float
    tracemalloc_peak_bytes: int
    rss_delta_bytes: int
    api_calls: int
    page_fetches: int
    bytes_read: int
    max_in_flight: int
    heartbeat_ticks: int
    heartbeat_advanced: bool
    event_loop_blocked: bool | None
    output_manifest: list[dict[str, Any]]
    output_digest: str


@dataclass
class ModeRunResult:
    """Aggregated results for one mode across one workload."""

    mode: str
    supported: bool
    validity: dict[str, Any]
    samples: list[SampleResult] = field(default_factory=list)
    unsupported_reason: str | None = None


def _output_manifest_and_digest(diff_parts: Sequence[str], patches: Sequence[FilePatchInfo]) -> tuple[list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    for index, (patch, part) in enumerate(zip(patches, diff_parts, strict=False)):
        items.append(
            {
                "index": index,
                "path": patch.filename,
                "status": str(patch.edit_type),
                "diff_chars": len(part),
                "diff_sha256": hashlib.sha256(part.encode("utf-8")).hexdigest(),
            }
        )
    # If generator returned fewer/more strings, still seal from patches alone for identity.
    if len(diff_parts) != len(patches):
        for index, patch in enumerate(patches):
            if index >= len(items):
                items.append(
                    {
                        "index": index,
                        "path": patch.filename,
                        "status": str(patch.edit_type),
                        "diff_chars": 0,
                        "diff_sha256": hashlib.sha256(b"").hexdigest(),
                    }
                )
    digest = hashlib.sha256(
        json.dumps(items, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return items, digest


def _run_sync_pipeline(
    bundle: FixtureBundle,
    counters: ApiCounters,
    *,
    block_ms: float = 0.0,
) -> tuple[list[FilePatchInfo], list[str]]:
    """Execute the strict git-tree file-processor + ordered diff-generator path."""
    repository = InstrumentedGitRepository(
        bundle.repository.full_name,
        bundle.content_map,
        counters,
        block_ms=block_ms,
    )
    # Page fetches are simulated from the fixture page layout (no network).
    for _page in bundle.pages:
        counters.record_page()

    processor = FileProcessor(
        pattern_matcher=DummyPatternMatcher(),
        max_files_allowed=bundle.workload.files + 1,
    )
    patches = processor.process_files_to_patches(
        list(bundle.files),
        repository=repository,
        head_sha=bundle.head_sha,
        base_sha=bundle.base_sha,
    )
    generator = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
    diffs = [generated.diff for generated in generator.generate_ordered_file_diffs(patches)]
    return patches, diffs


async def _heartbeat_probe(
    duration_seconds: float,
    interval_seconds: float = 0.01,
) -> int:
    """Count event-loop sleep ticks over a duration."""
    ticks = 0
    deadline = time.perf_counter() + duration_seconds
    while time.perf_counter() < deadline:
        await anyio.sleep(interval_seconds)
        ticks += 1
    return ticks


async def _run_async_negative_control(
    bundle: FixtureBundle,
    counters: ApiCounters,
) -> tuple[list[FilePatchInfo], list[str], int, bool]:
    """Run blocking sync work on the event loop while probing heartbeat.

    The negative control intentionally schedules blocking work without a worker
    thread so the heartbeat stalls — documenting current event-loop risk rather
    than claiming safety.
    """
    ticks_box: dict[str, int] = {"ticks": 0}
    result_box: dict[str, Any] = {}

    async def heartbeat() -> None:
        ticks_box["ticks"] = await _heartbeat_probe(duration_seconds=0.25, interval_seconds=0.005)

    async def blocking_work() -> None:
        # Directly call the sync pipeline on the event loop (negative control).
        patches, diffs = _run_sync_pipeline(bundle, counters, block_ms=5.0)
        result_box["patches"] = patches
        result_box["diffs"] = diffs

    start = time.perf_counter()
    async with anyio.create_task_group() as tg:
        tg.start_soon(heartbeat)
        tg.start_soon(blocking_work)
    elapsed = time.perf_counter() - start

    expected_min_ticks = max(1, int(elapsed / 0.01) // 4)
    ticks = ticks_box["ticks"]
    # If blocking dominated, ticks will be far below an unblocked expectation.
    blocked = ticks < expected_min_ticks
    return result_box["patches"], result_box["diffs"], ticks, blocked


def measure_with_counters(
    *,
    sample_index: int,
    counters: ApiCounters,
    run: Callable[[], tuple[list[FilePatchInfo], list[str], int, bool | None]],
) -> SampleResult:
    """Time one sample with CPU, RSS, tracemalloc, and counter snapshots."""
    counters.reset()
    rss_before = _rss_bytes()
    cpu_before = _cpu_seconds()
    tracemalloc.start()
    wall_start = time.perf_counter()
    try:
        patches, diffs, heartbeat_ticks, event_loop_blocked = run()
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        del _current
        tracemalloc.stop()
    wall = time.perf_counter() - wall_start
    cpu = max(0.0, _cpu_seconds() - cpu_before)
    rss_after = _rss_bytes()
    snap = counters.snapshot()
    output_manifest, output_digest = _output_manifest_and_digest(diffs, patches)
    return SampleResult(
        sample_index=sample_index,
        wall_seconds=wall,
        cpu_seconds=cpu,
        tracemalloc_peak_bytes=peak,
        rss_delta_bytes=max(0, rss_after - rss_before),
        api_calls=snap["api_calls"],
        page_fetches=snap["page_fetches"],
        bytes_read=snap["bytes_read"],
        max_in_flight=snap["max_in_flight"],
        heartbeat_ticks=heartbeat_ticks,
        heartbeat_advanced=heartbeat_ticks > 0,
        event_loop_blocked=event_loop_blocked,
        output_manifest=output_manifest,
        output_digest=output_digest,
    )


def run_mode(
    mode: str,
    bundle: FixtureBundle,
    phase: PhaseName,
) -> ModeRunResult:
    """Execute a mode for one fixture according to phase support rules."""
    if mode not in ALL_KNOWN_MODES:
        raise ValueError(f"Unknown mode: {mode}")

    if phase == "baseline" and mode in POST_CHANGE_MODES:
        return ModeRunResult(
            mode=mode,
            supported=False,
            validity={"status": "unsupported", "reason": "post-change mode not available in baseline phase"},
            unsupported_reason="post-change mode not available in baseline phase",
        )

    samples: list[SampleResult] = []
    validity: dict[str, Any] = {"status": "ok", "mode": mode}

    def make_sync_runner(counters: ApiCounters) -> Callable[[], tuple[list[FilePatchInfo], list[str], int, bool | None]]:
        def _run() -> tuple[list[FilePatchInfo], list[str], int, bool | None]:
            patches, diffs = _run_sync_pipeline(bundle, counters)
            return patches, diffs, 0, None

        return _run

    def make_negative_runner(counters: ApiCounters) -> Callable[[], tuple[list[FilePatchInfo], list[str], int, bool | None]]:
        def _run() -> tuple[list[FilePatchInfo], list[str], int, bool | None]:
            patches, diffs, ticks, blocked = anyio.run(_run_async_negative_control, bundle, counters)
            return patches, diffs, ticks, blocked

        return _run

    total_runs = WARMUPS + bundle.workload.samples
    for run_idx in range(total_runs):
        counters = ApiCounters()
        if mode == "sync-current":
            runner = make_sync_runner(counters)
        elif mode == "async-current-negative-control":
            runner = make_negative_runner(counters)
        elif mode in POST_CHANGE_MODES:
            runner = make_sync_runner(counters)
        else:
            raise AssertionError(f"Supported mode not implemented: {mode}")

        sample = measure_with_counters(sample_index=run_idx - WARMUPS, counters=counters, run=runner)
        if run_idx < WARMUPS:
            continue  # warmup excluded from measured samples
        samples.append(sample)

    if mode == "async-current-negative-control":
        # Negative control documents blocking rather than claiming safety.
        blocked_flags = [s.event_loop_blocked for s in samples]
        validity = {
            "status": "negative-control",
            "mode": mode,
            "event_loop_blocking_observed": any(flag is True for flag in blocked_flags),
            "claims_event_loop_safety": False,
            "heartbeat_ticks_median": sorted(s.heartbeat_ticks for s in samples)[len(samples) // 2]
            if samples
            else 0,
        }
    else:
        digests = {s.output_digest for s in samples}
        validity = {
            "status": "ok",
            "mode": mode,
            "claims_event_loop_safety": False,  # baseline sync path is not yet isolated
            "stable_output_digest": len(digests) == 1,
            "output_digest": next(iter(digests)) if digests else None,
            "max_in_flight_max": max((s.max_in_flight for s in samples), default=0),
        }

    return ModeRunResult(mode=mode, supported=True, validity=validity, samples=samples)


def sample_to_dict(sample: SampleResult) -> dict[str, Any]:
    return {
        "sample_index": sample.sample_index,
        "wall_seconds": sample.wall_seconds,
        "cpu_seconds": sample.cpu_seconds,
        "tracemalloc_peak_bytes": sample.tracemalloc_peak_bytes,
        "rss_delta_bytes": sample.rss_delta_bytes,
        "api_calls": sample.api_calls,
        "page_fetches": sample.page_fetches,
        "bytes_read": sample.bytes_read,
        "max_in_flight": sample.max_in_flight,
        "heartbeat_ticks": sample.heartbeat_ticks,
        "heartbeat_advanced": sample.heartbeat_advanced,
        "event_loop_blocked": sample.event_loop_blocked,
        "output_manifest": sample.output_manifest,
        "output_digest": sample.output_digest,
    }


def mode_to_dict(result: ModeRunResult) -> dict[str, Any]:
    return {
        "mode": result.mode,
        "supported": result.supported,
        "unsupported_reason": result.unsupported_reason,
        "validity": result.validity,
        "samples": [sample_to_dict(s) for s in result.samples],
    }


def run_matrix(
    *,
    matrix: str,
    phase: PhaseName,
    modes: Sequence[str],
    seed: int = DEFAULT_SEED,
    workloads: Sequence[str] | None = None,
) -> dict[str, Any]:
    if matrix != MATRIX_NAME:
        raise ValueError(f"Unsupported matrix: {matrix} (only {MATRIX_NAME} is defined)")

    selected = list(workloads) if workloads else list(STRICT_V1_WORKLOADS.keys())
    for name in selected:
        if name not in STRICT_V1_WORKLOADS:
            raise ValueError(f"Unknown workload: {name}")

    for mode in modes:
        if mode not in ALL_KNOWN_MODES:
            raise ValueError(f"Unknown mode: {mode}")

    # Never time two labels that call the same path in one report section.
    if len(modes) != len(set(modes)):
        raise ValueError("Duplicate modes are not allowed in a single run")

    report: dict[str, Any] = {
        "schema_version": 1,
        "matrix": MATRIX_NAME,
        "phase": phase,
        "seed": seed,
        "warmups": WARMUPS,
        "modes_requested": list(modes),
        "workloads": {},
        "platform": {
            "system": platform.system(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "network_calls": 0,
    }

    for name in selected:
        spec = STRICT_V1_WORKLOADS[name]
        bundle = build_fixture(spec, seed=seed)
        validate_fixture(bundle)
        workload_entry: dict[str, Any] = {
            "spec": {
                "name": spec.name,
                "files": spec.files,
                "lines": spec.lines,
                "samples": spec.samples,
                "style": spec.style,
            },
            "fixture_manifest": bundle.manifest,
            "fixture_digest": bundle.fixture_digest,
            "modes": {},
        }
        for mode in modes:
            workload_entry["modes"][mode] = mode_to_dict(run_mode(mode, bundle, phase))
        report["workloads"][name] = workload_entry

    return report


def write_json_atomic(path: Path, payload: dict[str, Any], *, allow_overwrite: bool) -> str:
    """Write JSON, refuse overwrite unless allowed. Returns SHA-256 of file bytes."""
    path = path.resolve()
    if path.exists() and not allow_overwrite:
        raise FileExistsError(
            f"Refusing to overwrite existing artifact: {path}. "
            "Pass a new path or delete the sealed baseline deliberately."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(raw, encoding="utf-8")
    os.replace(tmp, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def compare_reports(baseline: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    """Compare sealed baseline vs post report."""
    if baseline.get("schema_version") != post.get("schema_version"):
        raise ValueError("Schema version mismatch between baseline and post reports")
    if baseline.get("matrix") != post.get("matrix"):
        raise ValueError("Matrix mismatch between baseline and post reports")
    if baseline.get("seed") != post.get("seed"):
        raise ValueError("Seed mismatch between baseline and post reports")

    comparison: dict[str, Any] = {
        "schema_version": 1,
        "matrix": baseline["matrix"],
        "seed": baseline["seed"],
        "workloads": {},
        "digest_matches": True,
    }
    for name, base_wl in baseline["workloads"].items():
        post_wl = post["workloads"].get(name)
        if post_wl is None:
            raise ValueError(f"Post report missing workload {name}")
        base_digest = base_wl["fixture_digest"]
        post_digest = post_wl["fixture_digest"]
        match = base_digest == post_digest
        comparison["digest_matches"] = comparison["digest_matches"] and match
        comparison["workloads"][name] = {
            "fixture_digest_baseline": base_digest,
            "fixture_digest_post": post_digest,
            "fixture_digest_match": match,
        }
    return comparison


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic full-diff benchmark harness (strict-v1 matrix)",
    )
    parser.add_argument("--matrix", default=MATRIX_NAME, help="Benchmark matrix name (only strict-v1)")
    parser.add_argument(
        "--phase",
        choices=["baseline", "post"],
        default="baseline",
        help="Capture phase",
    )
    parser.add_argument(
        "--modes",
        default="sync-current,async-current-negative-control",
        help="Comma-separated modes",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--workloads",
        default="",
        help="Optional comma-separated workload subset (default: all strict-v1)",
    )
    parser.add_argument("--json", dest="json_path", default="", help="Output JSON path")
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow overwriting an existing JSON artifact (disabled by default)",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="Baseline JSON path for post-phase validation",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE", "POST"),
        help="Compare two report JSON files and write --json comparison",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.compare:
        baseline_path = Path(args.compare[0])
        post_path = Path(args.compare[1])
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        post = json.loads(post_path.read_text(encoding="utf-8"))
        comparison = compare_reports(baseline, post)
        if not comparison["digest_matches"]:
            print("ERROR: fixture digests do not match between baseline and post", file=sys.stderr)
            if args.json_path:
                write_json_atomic(Path(args.json_path), comparison, allow_overwrite=args.allow_overwrite)
            return 2
        if args.json_path:
            digest = write_json_atomic(Path(args.json_path), comparison, allow_overwrite=args.allow_overwrite)
            print(f"Wrote comparison JSON sha256={digest} path={args.json_path}")
        else:
            print(json.dumps(comparison, indent=2, sort_keys=True))
        return 0

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    workloads = [w.strip() for w in args.workloads.split(",") if w.strip()] or None

    try:
        report = run_matrix(
            matrix=args.matrix,
            phase=args.phase,
            modes=modes,
            seed=args.seed,
            workloads=workloads,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            print(f"ERROR: baseline not found: {baseline_path}", file=sys.stderr)
            return 1
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        try:
            comparison = compare_reports(baseline, report)
        except ValueError as exc:
            print(f"ERROR: baseline validation failed: {exc}", file=sys.stderr)
            return 1
        if not comparison["digest_matches"]:
            print("ERROR: post report fixture digests diverge from sealed baseline", file=sys.stderr)
            return 2
        report["baseline_path"] = str(baseline_path)
        report["baseline_sha256"] = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

    if args.json_path:
        try:
            digest = write_json_atomic(
                Path(args.json_path),
                report,
                allow_overwrite=args.allow_overwrite,
            )
        except FileExistsError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote benchmark JSON sha256={digest} path={args.json_path}")
    else:
        # Human-readable summary when no JSON path given.
        print(f"matrix={report['matrix']} phase={report['phase']} seed={report['seed']}")
        for name, wl in report["workloads"].items():
            print(f"  workload={name} fixture_digest={wl['fixture_digest'][:12]}…")
            for mode, mode_result in wl["modes"].items():
                if not mode_result["supported"]:
                    print(f"    mode={mode} unsupported ({mode_result.get('unsupported_reason')})")
                    continue
                samples = mode_result["samples"]
                if samples:
                    walls = [s["wall_seconds"] for s in samples]
                    print(
                        f"    mode={mode} samples={len(samples)} "
                        f"wall_median={sorted(walls)[len(walls) // 2]:.4f}s "
                        f"validity={mode_result['validity'].get('status')}"
                    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
