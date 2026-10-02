"""
Tests for Phase 2 Improvements: Diff Builder Optimization

This module contains unit tests for Phase 2 improvements including:
- Chunked processing for large files
"""


# =============================================================================
# Phase 2.2: Chunked Processing Tests (diff_utils.py)
# =============================================================================


class TestChunkedProcessing:
    """Tests for chunked processing of large files."""

    def test_build_chunk_hunk_with_changes(self):
        """Test chunk hunk generation with actual changes."""
        from prdiffer.infrastructure.utils.diff_utils import DiffUtils

        diff_utils = DiffUtils()

        orig_lines = ["line1", "line2", "line3"]
        new_lines = ["line1", "modified", "line3"]

        result = diff_utils._build_chunk_hunk(orig_lines, new_lines, 1, 1)

        assert "@@ -1,3 +1,3 @@" in result
        assert "-line2" in result
        assert "+modified" in result

    def test_build_chunk_hunk_no_changes(self):
        """Equal-only chunks still emit full-context equal lines."""
        from prdiffer.infrastructure.utils.diff_utils import DiffUtils

        diff_utils = DiffUtils()

        orig_lines = ["line1", "line2"]
        new_lines = ["line1", "line2"]  # Same content

        result = diff_utils._build_chunk_hunk(orig_lines, new_lines, 1, 1)

        assert " line1" in result
        assert " line2" in result

    def test_build_full_file_patch_chunked_small_file(self):
        """Test chunked processing falls back to standard for small files."""
        from prdiffer.infrastructure.utils.diff_utils import DiffUtils

        diff_utils = DiffUtils()

        original = "line1\nline2"
        new = "line1\nmodified"

        result = diff_utils.build_full_file_patch_chunked(original, new, chunk_size=1000, large_file_threshold=5000)

        # Should return a valid diff
        assert "@@ -" in result

    def test_build_full_file_patch_chunked_large_file(self):
        """Test chunked processing for large files."""
        from prdiffer.infrastructure.utils.diff_utils import DiffUtils

        diff_utils = DiffUtils()

        # Create large files (just above threshold)
        original = "\n".join([f"original_line_{i}" for i in range(100)])
        new = "\n".join([f"new_line_{i}" for i in range(100)])

        result = diff_utils.build_full_file_patch_chunked(original, new, chunk_size=50, large_file_threshold=50)

        # Should return a valid diff with multiple hunks
        assert result != ""
        assert "@@ -" in result


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhase2Integration:
    """Integration tests for Phase 2 improvements."""

    def test_chunked_processing_maintains_diff_integrity(self):
        """Test that chunked processing produces valid diffs."""
        from prdiffer.infrastructure.utils.diff_utils import DiffUtils

        diff_utils = DiffUtils()

        # Create original and modified versions
        original_lines = [f"line_{i}" for i in range(200)]
        new_lines = original_lines.copy()
        new_lines[50] = "modified_line_50"
        new_lines[150] = "modified_line_150"

        original = "\n".join(original_lines)
        new = "\n".join(new_lines)

        result = diff_utils.build_full_file_patch_chunked(original, new, chunk_size=100, large_file_threshold=100)

        # Should detect the modifications
        assert "-line_50" in result or "+modified_line_50" in result
