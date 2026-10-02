"""Comprehensive tests for PatternMatcher."""

from prdiffer.infrastructure.utils.pattern_matcher import PatternMatcher


class TestPatternMatcherInit:
    """Tests for PatternMatcher initialization."""

    def test_init_with_patterns(self):
        """Test initialization with ignore patterns."""
        patterns = ["*.lock", "node_modules/"]
        matcher = PatternMatcher(ignore_patterns=patterns)

        assert matcher.ignore_patterns == patterns
        assert matcher.valid_extensions == []

    def test_init_with_extensions(self):
        """Test initialization with valid extensions."""
        patterns = ["*.lock"]
        extensions = [".py", ".js"]
        matcher = PatternMatcher(ignore_patterns=patterns, valid_extensions=extensions)

        assert matcher.valid_extensions == extensions

    def test_init_empty_patterns(self):
        """Test initialization with empty patterns."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher.ignore_patterns == []
        assert len(matcher._compiled_patterns) == 0


class TestCompilePatterns:
    """Tests for _compile_patterns method."""

    def test_compile_simple_string(self):
        """Test compiling simple string patterns."""
        matcher = PatternMatcher(ignore_patterns=["exact_file.txt"])

        assert len(matcher._compiled_patterns) == 1
        pattern_type, pattern = matcher._compiled_patterns[0]
        assert pattern_type == "string"
        assert pattern == "exact_file.txt"

    def test_compile_wildcard_extension(self):
        """Test compiling wildcard extension patterns like *.lock."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        assert len(matcher._compiled_patterns) == 1
        pattern_type, pattern = matcher._compiled_patterns[0]
        assert pattern_type == "string"
        assert pattern == "*.lock"

    def test_compile_wildcard_prefix(self):
        """Test compiling wildcard prefix patterns like test_*."""
        matcher = PatternMatcher(ignore_patterns=["test_*"])

        assert len(matcher._compiled_patterns) == 1
        pattern_type, pattern = matcher._compiled_patterns[0]
        assert pattern_type == "regex"

    def test_compile_directory_pattern(self):
        """Test compiling directory patterns."""
        matcher = PatternMatcher(ignore_patterns=["node_modules/"])

        assert len(matcher._compiled_patterns) == 1
        pattern_type, pattern = matcher._compiled_patterns[0]
        assert pattern_type == "string"

    def test_compile_multiple_patterns(self):
        """Test compiling multiple patterns."""
        matcher = PatternMatcher(ignore_patterns=["*.lock", "test_*", "dist/"])

        assert len(matcher._compiled_patterns) == 3


class TestIsValidFile:
    """Tests for is_valid_file method."""

    def test_valid_file_no_patterns(self):
        """Test file is valid with no patterns."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher.is_valid_file("test.py") is True

    def test_file_matches_ignore_pattern(self):
        """Test file that matches ignore pattern."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        assert matcher.is_valid_file("package.lock") is False

    def test_file_does_not_match_ignore(self):
        """Test file that doesn't match ignore."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        assert matcher.is_valid_file("package.json") is True

    def test_file_with_valid_extension(self):
        """Test file with valid extension."""
        matcher = PatternMatcher(ignore_patterns=[], valid_extensions=[".py", ".js"])

        assert matcher.is_valid_file("test.py") is True
        assert matcher.is_valid_file("app.js") is True

    def test_file_with_invalid_extension(self):
        """Test file with invalid extension."""
        matcher = PatternMatcher(ignore_patterns=[], valid_extensions=[".py"])

        assert matcher.is_valid_file("test.txt") is False

    def test_file_ignored_and_wrong_extension(self):
        """Test file that is both ignored and has wrong extension."""
        matcher = PatternMatcher(
            ignore_patterns=["*.lock"],
            valid_extensions=[".py"],
        )

        assert matcher.is_valid_file("test.lock") is False

    def test_directory_pattern_ignored(self):
        """Test directory pattern ignores files in directory."""
        matcher = PatternMatcher(ignore_patterns=["node_modules/"])

        assert matcher.is_valid_file("node_modules/package.json") is False
        assert matcher.is_valid_file("src/package.json") is True


class TestMatchesAnyIgnorePattern:
    """Tests for _matches_any_ignore_pattern method."""

    def test_matches_simple_pattern(self):
        """Test matching simple pattern."""
        matcher = PatternMatcher(ignore_patterns=["exact_file.txt"])

        assert matcher._matches_any_ignore_pattern("exact_file.txt") is True
        assert matcher._matches_any_ignore_pattern("other_file.txt") is False

    def test_matches_wildcard_extension(self):
        """Test matching wildcard extension pattern."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        assert matcher._matches_any_ignore_pattern("package.lock") is True
        assert matcher._matches_any_ignore_pattern("yarn.lock") is True
        assert matcher._matches_any_ignore_pattern("package.json") is False

    def test_matches_wildcard_prefix_regex(self):
        """Test matching wildcard prefix pattern (regex)."""
        matcher = PatternMatcher(ignore_patterns=["test_*"])

        assert matcher._matches_any_ignore_pattern("test_file.py") is True
        assert matcher._matches_any_ignore_pattern("test_unit.py") is True
        assert matcher._matches_any_ignore_pattern("main.py") is False

    def test_matches_directory_pattern(self):
        """Test matching directory pattern."""
        matcher = PatternMatcher(ignore_patterns=["dist/"])

        assert matcher._matches_any_ignore_pattern("dist/bundle.js") is True
        assert matcher._matches_any_ignore_pattern("src/bundle.js") is False


class TestMatchesStringPattern:
    """Tests for _matches_string_pattern method."""

    def test_directory_pattern(self):
        """Test directory pattern matching."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher._matches_string_pattern("dist/bundle.js", "dist/") is True
        assert matcher._matches_string_pattern("src/bundle.js", "dist/") is False

    def test_file_pattern(self):
        """Test file pattern matching."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher._matches_string_pattern("test.py", "test.py") is True
        assert matcher._matches_string_pattern("other.py", "test.py") is False


class TestMatchesPattern:
    """Tests for _matches_pattern method."""

    def test_exact_match(self):
        """Test exact filename match."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher._matches_pattern("exact.txt", "exact.txt") is True
        assert matcher._matches_pattern("other.txt", "exact.txt") is False

    def test_wildcard_extension(self):
        """Test wildcard extension matching."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher._matches_pattern("file.lock", "*.lock") is True
        assert matcher._matches_pattern("file.json", "*.lock") is False
        assert matcher._matches_pattern("yarn.lock", "*.lock") is True

    def test_wildcard_in_middle(self):
        """Test wildcard in middle of pattern."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher._matches_pattern("test_file.py", "test_*.py") is True
        assert matcher._matches_pattern("main_file.py", "test_*.py") is False

    def test_suffix_match(self):
        """Test simple suffix matching."""
        matcher = PatternMatcher(ignore_patterns=[])

        assert matcher._matches_pattern("myfile.py", ".py") is True
        assert matcher._matches_pattern("myfile.txt", ".py") is False


class TestFilterFiles:
    """Tests for filter_files method."""

    def test_filter_empty_list(self):
        """Test filtering empty list."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        result = matcher.filter_files([])
        assert result == []

    def test_filter_all_valid(self):
        """Test filtering where all files are valid."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        files = ["main.py", "app.js", "test.py"]
        result = matcher.filter_files(files)

        assert result == files

    def test_filter_some_ignored(self):
        """Test filtering where some files are ignored."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        files = ["main.py", "package.lock", "yarn.lock", "app.js"]
        result = matcher.filter_files(files)

        assert result == ["main.py", "app.js"]

    def test_filter_with_extensions(self):
        """Test filtering with valid extensions."""
        matcher = PatternMatcher(
            ignore_patterns=["*.lock"],
            valid_extensions=[".py"],
        )

        files = ["main.py", "app.js", "test.py", "package.lock"]
        result = matcher.filter_files(files)

        assert result == ["main.py", "test.py"]

    def test_filter_with_directory_pattern(self):
        """Test filtering with directory patterns."""
        matcher = PatternMatcher(ignore_patterns=["node_modules/", "dist/"])

        files = [
            "src/main.py",
            "node_modules/lib.js",
            "dist/bundle.js",
            "tests/test.py",
        ]
        result = matcher.filter_files(files)

        assert result == ["src/main.py", "tests/test.py"]


class TestAgentsMdIgnorePatterns:
    """GITHUB_IGNORE_PATTERNS-style globs for nested AGENTS.md paths."""

    def test_star_agents_md_ignores_nested_paths(self):
        matcher = PatternMatcher(ignore_patterns=["*AGENTS.md"])

        assert matcher.is_valid_file("AGENTS.md") is False
        assert matcher.is_valid_file("prdiffer/domain/AGENTS.md") is False
        assert matcher.is_valid_file("tests/unit/infrastructure/utils/AGENTS.md") is False
        assert matcher.is_valid_file("prdiffer/domain/entities/file_content.py") is True

    def test_exact_agents_md_name_ignores_nested_paths(self):
        matcher = PatternMatcher(ignore_patterns=["AGENTS.md"])

        assert matcher.is_valid_file("AGENTS.md") is False
        assert matcher.is_valid_file("prdiffer/domain/AGENTS.md") is False
        assert matcher.is_valid_file("README.md") is True

    def test_nested_directory_segment_match(self):
        matcher = PatternMatcher(ignore_patterns=["node_modules/"])

        assert matcher.is_valid_file("src/node_modules/pkg/index.js") is False
        assert matcher.is_valid_file("node_modules/pkg/index.js") is False
        assert matcher.is_valid_file("src/app.js") is True


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_filename(self):
        """Test with empty filename."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        assert matcher.is_valid_file("") is True

    def test_special_characters_in_filename(self):
        """Test filename with special characters."""
        matcher = PatternMatcher(ignore_patterns=["test-*"])

        assert matcher.is_valid_file("test-file.py") is False
        assert matcher.is_valid_file("test_file.py") is True

    def test_unicode_filename(self):
        """Test unicode filename."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])

        assert matcher.is_valid_file("文件.py") is True
        assert matcher.is_valid_file("文件.lock") is False

    def test_long_filename(self):
        """Test very long filename."""
        matcher = PatternMatcher(ignore_patterns=["*.lock"])
        long_name = "a" * 500 + ".py"

        assert matcher.is_valid_file(long_name) is True

    def test_path_with_multiple_dirs(self):
        """Test path with multiple directories."""
        matcher = PatternMatcher(ignore_patterns=["node_modules/"])

        assert matcher.is_valid_file("node_modules/lib/file.js") is False
        assert matcher.is_valid_file("project/src/lib/file.js") is True

    def test_case_sensitive_matching(self):
        """Test case sensitivity."""
        matcher = PatternMatcher(ignore_patterns=["*.Lock"])

        assert matcher.is_valid_file("file.Lock") is False
        assert matcher.is_valid_file("file.lock") is True
