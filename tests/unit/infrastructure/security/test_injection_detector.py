"""Comprehensive tests for injection_detector.py."""

from prdiffer.infrastructure.security.injection_detector import (
    InjectionDetector,
    _detector,
)


class TestInjectionDetectorInit:
    """Tests for InjectionDetector initialization."""

    def test_init_uses_precompiled_default_patterns(self):
        """Instances detect with the class-level precompiled default regexes."""
        detector = InjectionDetector()
        assert detector._COMMAND_INJECTION_COMPILED is InjectionDetector._COMMAND_INJECTION_COMPILED
        assert detector._PATH_TRAVERSAL_COMPILED is InjectionDetector._PATH_TRAVERSAL_COMPILED
        assert detector._SQL_INJECTION_COMPILED is InjectionDetector._SQL_INJECTION_COMPILED


class TestCheckSuspiciousPatterns:
    """Tests for check_suspicious_patterns method."""

    def test_clean_input_returns_false(self):
        """Test clean input returns False."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("normal text") is False

    def test_command_injection_detected(self):
        """Test command injection is detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("$(whoami)") is True
        assert detector.check_suspicious_patterns("test; ls") is True
        assert detector.check_suspicious_patterns("test | cat") is True
        assert detector.check_suspicious_patterns("`id`") is True

    def test_path_traversal_detected(self):
        """Test path traversal is detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("../../../etc/passwd") is True
        assert detector.check_suspicious_patterns("~/secret") is True
        assert detector.check_suspicious_patterns("/etc/passwd") is True
        assert detector.check_suspicious_patterns("C:\\Windows") is True

    def test_sql_injection_detected(self):
        """Test SQL injection is detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("SELECT * FROM users") is True
        assert detector.check_suspicious_patterns("1; DROP TABLE users") is True
        assert detector.check_suspicious_patterns("-- comment") is True
        assert detector.check_suspicious_patterns("UNION SELECT") is True

    def test_windows_relative_and_unc_paths_detected(self):
        """Default path-traversal regex flags `.\\`, `..\\` and UNC prefixes."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns(".\\config") is True
        assert detector.check_suspicious_patterns("..\\config") is True
        assert detector.check_suspicious_patterns("\\\\server\\share") is True

    def test_empty_string(self):
        """Test empty string returns False."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("") is False


class TestGlobalDetectorDetection:
    """Tests for the module-level detector used by InputSanitizer."""

    def test_clean_input_returns_false(self):
        assert _detector.check_suspicious_patterns("normal text") is False

    def test_command_injection_detected(self):
        assert _detector.check_suspicious_patterns("$(whoami)") is True

    def test_path_traversal_detected(self):
        assert _detector.check_suspicious_patterns("../../../etc") is True

    def test_sql_injection_detected(self):
        assert _detector.check_suspicious_patterns("SELECT *") is True


class TestGlobalDetector:
    """Tests for global detector instance."""

    def test_global_detector_exists(self):
        """Test global detector instance exists."""
        assert _detector is not None
        assert isinstance(_detector, InjectionDetector)

    def test_global_detector_uses_defaults(self):
        """Test global detector uses the precompiled default patterns."""
        assert _detector._SQL_INJECTION_COMPILED is InjectionDetector._SQL_INJECTION_COMPILED


class TestCommandInjectionPatterns:
    """Detailed tests for command injection patterns."""

    def test_shell_metacharacters(self):
        """Test shell metacharacters are detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("test; command") is True
        assert detector.check_suspicious_patterns("test & command") is True
        assert detector.check_suspicious_patterns("test | command") is True
        assert detector.check_suspicious_patterns("test $var") is True

    def test_command_substitution(self):
        """Test command substitution is detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("$(command)") is True

    def test_backticks(self):
        """Test backticks are detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("`command`") is True


class TestPathTraversalPatterns:
    """Detailed tests for path traversal patterns."""

    def test_parent_directory_unix(self):
        """Test Unix parent directory patterns."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("../../../etc/passwd") is True
        assert detector.check_suspicious_patterns("..\\..\\windows") is True

    def test_home_directory(self):
        """Test home directory pattern."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("~/secrets") is True

    def test_system_directories(self):
        """Test system directory patterns."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("/etc/passwd") is True
        assert detector.check_suspicious_patterns("/var/log") is True
        assert detector.check_suspicious_patterns("/usr/bin") is True

    def test_windows_paths(self):
        """Test Windows path patterns."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("C:\\Windows") is True
        assert detector.check_suspicious_patterns("D:\\Data") is True


class TestSQLInjectionPatterns:
    """Detailed tests for SQL injection patterns."""

    def test_sql_comments(self):
        """Test SQL comment patterns."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("-- comment") is True
        assert detector.check_suspicious_patterns("# comment") is True
        assert detector.check_suspicious_patterns("/* comment */") is True

    def test_sql_keywords(self):
        """Test SQL keyword patterns."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("SELECT * FROM users") is True
        assert detector.check_suspicious_patterns("INSERT INTO") is True
        assert detector.check_suspicious_patterns("UPDATE users") is True
        assert detector.check_suspicious_patterns("DELETE FROM") is True
        assert detector.check_suspicious_patterns("DROP TABLE") is True
        assert detector.check_suspicious_patterns("CREATE TABLE") is True
        assert detector.check_suspicious_patterns("ALTER TABLE") is True
        assert detector.check_suspicious_patterns("UNION SELECT") is True

    def test_stored_procedures(self):
        """Test stored procedure patterns."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("EXEC sp_help") is True
        assert detector.check_suspicious_patterns("xp_cmdshell") is True


class TestEdgeCases:
    """Tests for edge cases."""

    def test_partial_sql_keywords_not_detected(self):
        """Test partial SQL keywords are not detected."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("selection") is False
        assert detector.check_suspicious_patterns("insertion") is False
        assert detector.check_suspicious_patterns("universal") is False

    def test_url_with_valid_chars(self):
        """Test valid URLs are not flagged."""
        detector = InjectionDetector()
        result = detector.check_suspicious_patterns("https://github.com/owner/repo/pull/123")
        assert result is False

    def test_mixed_patterns(self):
        """Test input with multiple pattern types."""
        detector = InjectionDetector()
        assert detector.check_suspicious_patterns("$(cmd) and ../etc") is True
