"""
Tests for Phase 3 Improvements: API Enhancement

This module contains unit tests for Phase 3 improvements including:
- Extended FilePatchInfo data model
- Extended PRDiff data model
- Structured error codes
- Error handling utilities
"""


# =============================================================================
# Phase 3.1: Extended FilePatchInfo Tests
# =============================================================================


class TestFilePatchInfoExtensions:
    """Tests for extended FilePatchInfo data model."""

    def test_new_fields_have_defaults(self):
        """Test that new fields have sensible defaults."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            base_file="old",
            head_file="new",
            patch="@@ diff @@",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=1,
            num_minus_lines=1,
        )

        # New Phase 3 fields should have defaults
        assert patch.diff_metadata is None
        assert patch.code_smell_indicators is None
        assert patch.suggested_review_priority == "normal"

    def test_calculate_review_priority_high_for_security_files(self):
        """Test high priority for security-sensitive files."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        security_files = [
            "auth.py",
            "security/config.py",
            "password_manager.py",
            "token_handler.py",
            ".env.example",
        ]

        for filename in security_files:
            patch = FilePatchInfo(
                filename=filename,
                edit_type=EDIT_TYPE.MODIFIED,
                num_plus_lines=5,
                num_minus_lines=3,
            )
            assert patch.calculate_review_priority() == "high", f"Expected high for {filename}"

    def test_calculate_review_priority_high_for_large_changes(self):
        """Test high priority for large change sets."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="regular.py",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=80,
            num_minus_lines=50,  # 130 total changes > 100
        )

        assert patch.calculate_review_priority() == "high"

    def test_calculate_review_priority_low_for_docs(self):
        """Test low priority for documentation files."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        doc_files = ["README.md", "docs/api.md", "CHANGELOG.txt", "LICENSE"]
        # Provide enough base_file content to keep change_percentage < 50%
        base_content = "\n".join([f"line {i}" for i in range(100)])

        for filename in doc_files:
            patch = FilePatchInfo(
                filename=filename,
                base_file=base_content,
                edit_type=EDIT_TYPE.MODIFIED,
                num_plus_lines=5,
                num_minus_lines=3,
            )
            assert patch.calculate_review_priority() == "low", f"Expected low for {filename}"

    def test_calculate_review_priority_low_for_tests(self):
        """Test low priority for test files."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        test_files = ["test_main.py", "tests/unit/test_api.py", "spec/test_helper.js"]
        # Provide enough base_file content to keep change_percentage < 50%
        base_content = "\n".join([f"line {i}" for i in range(100)])

        for filename in test_files:
            patch = FilePatchInfo(
                filename=filename,
                base_file=base_content,
                edit_type=EDIT_TYPE.MODIFIED,
                num_plus_lines=10,
                num_minus_lines=5,
            )
            assert patch.calculate_review_priority() == "low", f"Expected low for {filename}"

    def test_calculate_review_priority_normal_for_regular_files(self):
        """Test normal priority for regular source files."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        # Provide enough base_file content to keep change_percentage < 50%
        base_content = "\n".join([f"line {i}" for i in range(100)])

        patch = FilePatchInfo(
            filename="src/utils.py",
            base_file=base_content,
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=10,
            num_minus_lines=5,
        )

        assert patch.calculate_review_priority() == "normal"

    def test_detect_code_smells_todo(self):
        """Test detection of TODO comments."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            patch="+# TODO: fix this later\n+def broken():\n+    pass",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=3,
            num_minus_lines=0,
        )

        smells = patch.detect_code_smells()
        assert any("TODO" in s for s in smells)

    def test_detect_code_smells_fixme(self):
        """Test detection of FIXME comments."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            patch="+# FIXME: this is broken\n+return None",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=2,
            num_minus_lines=0,
        )

        smells = patch.detect_code_smells()
        assert any("FIXME" in s for s in smells)

    def test_detect_code_smells_debug_statements(self):
        """Test detection of debug statements."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            patch="+print('debugging')\n+console.log('test')",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=2,
            num_minus_lines=0,
        )

        smells = patch.detect_code_smells()
        assert any("print" in s for s in smells) or any("console.log" in s for s in smells)

    def test_detect_code_smells_large_changes(self):
        """Test detection of very large change sets."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            patch="@@ -1,500 +1,600 @@",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=600,
            num_minus_lines=0,
        )

        smells = patch.detect_code_smells()
        assert any("large" in s.lower() for s in smells)

    def test_detect_code_smells_empty_patch(self):
        """Test that empty patch returns no smells."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            patch="",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=0,
            num_minus_lines=0,
        )

        smells = patch.detect_code_smells()
        assert smells == []

    def test_get_summary_includes_new_fields(self):
        """Test that get_summary includes new Phase 3 fields."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        patch = FilePatchInfo(
            filename="test.py",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=5,
            num_minus_lines=3,
            suggested_review_priority="high",
            code_smell_indicators=("Contains TODO",),
        )

        summary = patch.get_summary()

        assert "review_priority" in summary
        assert "code_smell_indicators" in summary
        assert summary["review_priority"] == "high"
        assert summary["code_smell_indicators"] == ("Contains TODO",)


# =============================================================================
# Phase 3.1: Extended PRDiff Tests
# =============================================================================


class TestPRDiffExtensions:
    """Tests for extended PRDiff data model."""

    def test_has_files_true(self):
        """Test that PRDiff can have files."""
        from prdiffer.domain.entities.pr_diff import PRDiff
        from prdiffer.domain.entities.file_diff_response import (
            FileDiffResponse,
            FileStats,
        )
        from prdiffer.domain.entities.file_patch import EDIT_TYPE

        diff = PRDiff(
            head_sha="c" * 40,
            files=(
                FileDiffResponse(
                    path="test.py",
                    status=EDIT_TYPE.MODIFIED,
                    stats=FileStats(additions=1, deletions=1),
                    diff="@@ -1,3 +1,3 @@",
                ),
            ),
        )
        assert len(diff.files) == 1

    def test_has_files_false_empty(self):
        """Test that PRDiff can be empty."""
        from prdiffer.domain.entities.pr_diff import PRDiff

        diff = PRDiff(files=(), head_sha="c" * 40)
        assert len(diff.files) == 0


# =============================================================================
# Phase 3.2: Structured Error Codes Tests
# =============================================================================


class TestStructuredErrorCodes:
    """Tests for structured error code system."""

    def test_error_code_string_format(self):
        """Test error code string representation."""
        from prdiffer.domain.error_codes import E1001_INVALID_URL

        assert str(E1001_INVALID_URL) == "E1001_INVALID_URL"

    def test_input_validation_errors(self):
        """Test input validation error codes."""
        from prdiffer.domain.errors import ErrorCategory
        from prdiffer.domain.error_codes import (
            E1001_INVALID_URL,
            E1002_INVALID_REPOSITORY,
            E1003_INVALID_PR_NUMBER,
            E1004_SUSPICIOUS_INPUT,
        )

        errors = [
            E1001_INVALID_URL,
            E1002_INVALID_REPOSITORY,
            E1003_INVALID_PR_NUMBER,
            E1004_SUSPICIOUS_INPUT,
        ]

        for error in errors:
            assert error.category == ErrorCategory.INPUT_VALIDATION
            assert error.code.startswith("E1")

    def test_authentication_errors(self):
        """Test authentication error codes."""
        from prdiffer.domain.errors import ErrorCategory
        from prdiffer.domain.error_codes import (
            E2001_AUTH_REQUIRED,
            E2002_AUTH_FAILED,
            E2003_INSUFFICIENT_PERMISSIONS,
        )

        errors = [
            E2001_AUTH_REQUIRED,
            E2002_AUTH_FAILED,
            E2003_INSUFFICIENT_PERMISSIONS,
        ]

        for error in errors:
            assert error.category == ErrorCategory.AUTHENTICATION
            assert error.code.startswith("E2")

    def test_rate_limiting_errors(self):
        """Test rate limiting error codes."""
        from prdiffer.domain.errors import ErrorCategory
        from prdiffer.domain.error_codes import (
            E3001_RATE_LIMITED,
            E3002_SECONDARY_RATE_LIMIT,
        )

        errors = [E3001_RATE_LIMITED, E3002_SECONDARY_RATE_LIMIT]

        for error in errors:
            assert error.category == ErrorCategory.RATE_LIMITING
            assert error.code.startswith("E3")

    def test_not_found_errors(self):
        """Test resource not found error codes."""
        from prdiffer.domain.errors import ErrorCategory
        from prdiffer.domain.error_codes import (
            E4001_REPO_NOT_FOUND,
            E4002_PR_NOT_FOUND,
            E4003_FILE_NOT_FOUND,
        )

        errors = [E4001_REPO_NOT_FOUND, E4002_PR_NOT_FOUND, E4003_FILE_NOT_FOUND]

        for error in errors:
            assert error.category == ErrorCategory.NOT_FOUND
            assert error.code.startswith("E4")

    def test_internal_errors(self):
        """Test internal server error codes."""
        from prdiffer.domain.errors import ErrorCategory
        from prdiffer.domain.error_codes import (
            E5001_INTERNAL_ERROR,
            E5002_GITHUB_API_ERROR,
            E5003_DIFF_GENERATION_ERROR,
        )

        errors = [
            E5001_INTERNAL_ERROR,
            E5002_GITHUB_API_ERROR,
            E5003_DIFF_GENERATION_ERROR,
        ]

        for error in errors:
            assert error.category == ErrorCategory.INTERNAL
            assert error.code.startswith("E5")


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhase3Integration:
    """Integration tests for Phase 3 improvements."""

    def test_file_patch_with_priority_and_smells(self):
        """Test FilePatchInfo with auto-detected priority and smells."""
        from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE

        # Create a security-related file with code smells
        patch = FilePatchInfo(
            filename="auth/token_handler.py",
            base_file="original code",
            head_file="new code",
            patch="+# TODO: add proper validation\n+password = 'hardcoded'\n+print('debug')",
            edit_type=EDIT_TYPE.MODIFIED,
            num_plus_lines=3,
            num_minus_lines=0,
        )

        # Calculate priority
        priority = patch.calculate_review_priority()
        assert priority == "high"  # Security file

        # Detect smells
        smells = patch.detect_code_smells()
        assert len(smells) > 0  # Should detect TODO and print

    def test_pr_diff_simplified(self):
        """Test PRDiff with simplified structure."""
        from prdiffer.domain.entities.pr_diff import PRDiff
        from prdiffer.domain.entities.file_diff_response import (
            FileDiffResponse,
            FileStats,
        )
        from prdiffer.domain.entities.file_patch import EDIT_TYPE

        diff = PRDiff(
            head_sha="c" * 40,
            files=(
                FileDiffResponse(
                    path="test.py",
                    status=EDIT_TYPE.MODIFIED,
                    stats=FileStats(additions=1, deletions=1),
                    diff="@@ -1,10 +1,15 @@\n-old\n+new",
                ),
            ),
        )

        # Verify files array works correctly
        assert len(diff.files) == 1
        assert diff.files[0].diff.startswith("@@")
