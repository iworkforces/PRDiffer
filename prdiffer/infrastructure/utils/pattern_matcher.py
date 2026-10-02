"""Pattern matching utility for file filtering and validation."""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface


class PatternMatcher(PatternMatchingServiceInterface):
    """Pattern matcher for file filtering with wildcard support.

    Patterns are matched against the full repository-relative path **and** the
    basename (so ``*AGENTS.md`` / ``AGENTS.md`` ignore nested ``…/AGENTS.md``).
    Directory patterns ending in ``/`` match that path segment anywhere.
    """

    def __init__(self, ignore_patterns: list[str], valid_extensions: list[str] | None = None):
        self.ignore_patterns = ignore_patterns
        self.valid_extensions = valid_extensions or []
        self._compiled_patterns = self._compile_patterns(ignore_patterns)

    def _compile_patterns(self, patterns: list[str]) -> list[tuple[str, str | re.Pattern[str]]]:
        compiled: list[tuple[str, str | re.Pattern[str]]] = []
        for pattern in patterns:
            # Explicit regex (settings.toml hidden-file rule, etc.)
            if pattern.startswith("(") or pattern.startswith("^"):
                try:
                    compiled.append(("regex", re.compile(pattern + ("" if pattern.endswith("$") else "$"))))
                    continue
                except re.error:
                    compiled.append(("string", pattern))
                    continue
            # Globs that are not simple ``*.ext`` — compile as end-anchored regex.
            # ``*AGENTS.md`` must match nested paths (``pkg/AGENTS.md``).
            if "*" in pattern and not pattern.startswith("*."):
                regex_pattern = pattern.replace("**", "*").replace("*", ".*")
                try:
                    compiled_regex = re.compile(regex_pattern + "$")
                    compiled.append(("regex", compiled_regex))
                except re.error:
                    compiled.append(("string", pattern))
            else:
                compiled.append(("string", pattern))
        return compiled

    def is_valid_file(self, filename: str) -> bool:
        """Check if a file should be processed based on configured patterns."""
        if self._matches_any_ignore_pattern(filename):
            return False

        if self.valid_extensions:
            has_valid_extension = any(filename.endswith(ext) for ext in self.valid_extensions)
            if not has_valid_extension:
                return False

        return True

    def _path_candidates(self, filename: str) -> tuple[str, str]:
        """Return (posix_full_path, basename) for matching."""
        normalized = filename.replace("\\", "/")
        base = PurePosixPath(normalized).name
        return normalized, base

    def _matches_any_ignore_pattern(self, filename: str) -> bool:
        """Check if filename matches any ignore pattern (full path or basename)."""
        full, base = self._path_candidates(filename)
        for pattern_type, pattern in self._compiled_patterns:
            if pattern_type == "regex":
                if isinstance(pattern, re.Pattern) and (pattern.match(full) or pattern.match(base)):
                    return True
            else:
                if isinstance(pattern, str) and (
                    self._matches_string_pattern(full, pattern) or self._matches_string_pattern(base, pattern)
                ):
                    return True
        return False

    def _matches_string_pattern(self, filename: str, pattern: str) -> bool:
        """Check if filename matches a string / glob pattern."""
        if pattern.endswith("/"):
            segment = pattern[:-1]
            if not segment:
                return False
            # Match directory segment at root or nested: node_modules/… or …/node_modules/…
            return (
                filename == segment
                or filename.startswith(segment + "/")
                or f"/{segment}/" in f"/{filename}/"
            )
        return self._matches_pattern(filename, pattern)

    def _matches_pattern(self, filename: str, pattern: str) -> bool:
        """Check if filename matches a pattern, supporting wildcards."""
        if filename == pattern:
            return True

        if pattern.startswith("*."):
            extension = pattern[1:]
            return filename.endswith(extension)

        if "*" in pattern:
            # Prefer fnmatch for standard globs; also try basename-only patterns.
            if fnmatch.fnmatch(filename, pattern):
                return True
            regex_pattern = pattern.replace("**", "*").replace("*", ".*")
            return bool(re.match(regex_pattern + "$", filename))

        # Extension-like tokens (".py") and basename/suffix names ("AGENTS.md").
        if pattern.startswith("."):
            return filename.endswith(pattern)
        return filename == pattern or filename.endswith("/" + pattern)

    def filter_files(self, filenames: list[str]) -> list[str]:
        """Filter a list of filenames based on configured patterns."""
        return [filename for filename in filenames if self.is_valid_file(filename)]
