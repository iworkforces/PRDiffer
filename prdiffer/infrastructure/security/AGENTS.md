# AGENTS.md - Infrastructure/Security

**Package:** 0.6.2  
Input validation, injection detection, and sanitization (~200 lines).

## STRUCTURE
```
prdiffer/infrastructure/security/
├── input_validator.py            # InputValidator — implements InputValidatorProtocol (~88)
├── injection_detector.py         # InjectionDetector + module `_detector` (~37)
└── sanitizer.py                  # InputSanitizer (~71)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| **Validate PR URL / params** | `input_validator.py` | Domain `InputValidatorProtocol` (4 methods) |
| **GitHub URL** | `validate_github_url` | github.com PR paths |
| **GitLab URL** | `validate_gitlab_url` | Delegates to domain `parse_gitlab_merge_request_url` (`domain/entities/gitlab_merge_request_url.py`; custom hosts) |
| **Free-text input** | `sanitize_string` | Length, null bytes, injection patterns → E1xxx |
| **Threat patterns** | `injection_detector.py` | Precompiled command / path traversal / SQL-ish regexes |
| **Sanitize for logs** | `sanitize_for_logging` → `InputSanitizer` | Length-limited printable strings |

## CONVENTIONS
- Implements `InputValidatorProtocol` (domain): `validate_github_url`, `validate_gitlab_url`, `sanitize_string`, `sanitize_for_logging`.
- Detection patterns are the precompiled class regexes in `InjectionDetector` (not configurable via settings).
- Fail closed on suspicious input with domain validation errors / **E1xxx** codes.
- Prefer orchestration through `InputValidator` rather than calling detector/sanitizer ad hoc from tools.
- GitLab host **allowlist** is enforced later in `GitLabRuntime` / session open (not only here).

## ANTI-PATTERNS
- NO auth decisions based only on client-supplied claims without API keys.
- NO regex ReDoS-prone patterns without review.
- NO trusting unsanitized free-text for logs or shell/path construction.
- NO disabling injection checks for convenience in production paths.
