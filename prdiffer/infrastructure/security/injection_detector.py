"""Injection detection patterns for input security.

Detects SQL injection, command injection, and path traversal patterns using
precompiled default regexes.
"""

import re


class InjectionDetector:
    """Detects injection patterns in input strings."""

    _COMMAND_INJECTION_COMPILED = re.compile(
        r"[;&|`$]|\$\(|`|\|&|\|\||&&|%0[aAdD]|\\x[0-9a-fA-F]{2}",
        re.IGNORECASE,
    )
    _PATH_TRAVERSAL_COMPILED = re.compile(
        r"\.\.|~/|/etc/|/var/|/usr/|[a-zA-Z]:\\|\.\\|\\\\|\.\.%2[fF]|\.\.%5[cC]|%2[eE]%2[eE]|%252[eE]",
        re.IGNORECASE,
    )
    _SQL_INJECTION_COMPILED = re.compile(
        r"(?:--|#|/\*|\*/)|\b(?:union|select|insert|update|delete|drop|create|alter)\b|(?:exec|execute|xp_)|['\"]\s*(?:OR|AND)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+|['\"]\s*(?:OR|AND)\s+['\"][^'\"]+['\"]?\s*=\s*['\"]|;\s*(?:DROP|DELETE|TRUNCATE|UPDATE)|%",
        re.IGNORECASE,
    )

    def check_suspicious_patterns(self, value: str) -> bool:
        """Check if value contains suspicious injection patterns."""
        if self._COMMAND_INJECTION_COMPILED.search(value):
            return True
        if self._PATH_TRAVERSAL_COMPILED.search(value):
            return True
        if self._SQL_INJECTION_COMPILED.search(value):
            return True
        return False


_detector = InjectionDetector()
