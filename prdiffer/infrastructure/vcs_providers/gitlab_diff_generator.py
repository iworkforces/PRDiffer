"""Assemble ordered GitLab full-context FileDiffResponse values."""

from __future__ import annotations

from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE, FilePatchInfo
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabFileContents
from prdiffer.infrastructure.vcs_providers.gitlab_inventory import GitLabInventoryFile


class GitLabDiffAssembler:
    """Convert inventory + typed contents into ordered full-context PRDiff."""

    def __init__(self, diff_generator: DiffGenerator) -> None:
        self._diff_generator = diff_generator

    def assemble(
        self,
        inventory: tuple[GitLabInventoryFile, ...],
        contents: tuple[GitLabFileContents, ...],
        *,
        head_sha: str,
    ) -> PRDiff:
        if len(inventory) != len(contents):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                message="Inventory/content length mismatch",
                observed=len(contents),
                limit=len(inventory),
            )

        patches: list[FilePatchInfo] = []
        for item, content in zip(inventory, contents, strict=True):
            if content.index != item.index:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                    message="Content index mismatch",
                    path=content.path,
                    observed=content.index,
                    limit=item.index,
                )
            path = content.path
            old_filename = content.previous_path if content.edit_type is EDIT_TYPE.RENAMED else None
            patches.append(
                FilePatchInfo(
                    filename=path,
                    base_file=content.base.text,
                    head_file=content.head.text,
                    patch="",  # never pass provider hunk text as output
                    edit_type=content.edit_type,
                    old_filename=old_filename,
                    old_mode=content.old_mode,
                    new_mode=content.new_mode,
                )
            )

        generated = self._diff_generator.generate_ordered_file_diffs(patches)
        if len(generated) != len(inventory):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                message="Generated diff count mismatch",
                observed=len(generated),
                limit=len(inventory),
            )

        responses: list[FileDiffResponse] = []
        for item, content, gen in zip(inventory, contents, generated, strict=True):
            if gen.index != item.index or gen.path != content.path:
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                    message="Generated path/index mismatch",
                    path=gen.path,
                )

            # Equal-content equal-mode modified is indeterminate (no textual or mode change).
            # Fail closed regardless of any incidental hunk text the generator may emit.
            if (
                content.edit_type is EDIT_TYPE.MODIFIED
                and content.base.text == content.head.text
                and (content.old_mode is None or content.old_mode == content.new_mode)
            ):
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                    message="Equal-content equal-mode modified is not a valid strict full-diff change",
                    path=content.path,
                )

            # Empty textual diff only allowed for authoritative zero-byte add/delete.
            if not (gen.diff or "").strip():
                if content.edit_type is EDIT_TYPE.ADDED and content.head.text == "":
                    pass
                elif content.edit_type is EDIT_TYPE.DELETED and content.base.text == "":
                    pass
                elif content.edit_type is EDIT_TYPE.RENAMED:
                    # rename-only must still have headers
                    if "rename from" not in (gen.diff or ""):
                        raise FullDiffIncompleteError(
                            FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                            message="Rename-only missing headers",
                            path=content.path,
                        )
                else:
                    raise FullDiffIncompleteError(
                        FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                        message="Empty full-context diff is not allowed for this status",
                        path=content.path,
                    )

            additions, deletions = _count_unified_stats(gen.diff)
            previous_path = gen.previous_path if content.edit_type is EDIT_TYPE.RENAMED else None
            responses.append(
                FileDiffResponse(
                    path=content.path,
                    status=content.edit_type,
                    stats=FileStats(additions=additions, deletions=deletions),
                    diff=gen.diff,
                    previous_path=previous_path,
                )
            )

        return PRDiff(files=tuple(responses), head_sha=head_sha)


def _count_unified_stats(diff: str) -> tuple[int, int]:
    """Count `+`/`-` source lines only inside ``@@`` hunks.

    File headers (``--- a/…`` / ``+++ b/…``) and mode/rename metadata before the
    first hunk are ignored. Source lines whose payload starts with ``++`` or
    ``--`` (unified form ``+++…`` / ``---…``) count normally once inside a hunk.
    """
    additions = 0
    deletions = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\"):
            # "\ No newline at end of file"
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions
