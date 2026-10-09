import logging
from typing import Any
import os
from pathlib import Path
from importlib.resources import files
from threading import RLock

from dotenv import load_dotenv
from importlib import import_module
from typing import Protocol
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.domain.config.gitlab_config import GitLabConfig

logger = logging.getLogger(__name__)


class _DynaconfSettings(Protocol):
    def get(self, key: str, default: object = None) -> object: ...

    def from_env(self, env: str) -> "_DynaconfSettings": ...

    @property
    def _loaded_files(self) -> list[str]: ...

    @property
    def settings_files(self) -> list[str]: ...


class _DynaconfFactory(Protocol):
    def __call__(
        self,
        *,
        settings_files: list[str],
        environments: bool,
        env_switcher: str,
        load_dotenv: bool,
    ) -> _DynaconfSettings: ...


Dynaconf: _DynaconfFactory = getattr(import_module("dynaconf"), "Dynaconf")


def project_root() -> Path:
    """Repository root (parent of the ``prdiffer`` package)."""
    # prdiffer/infrastructure/settings.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2]


def load_project_dotenv(*, override: bool = False) -> Path | None:
    """Load ``.env`` from the project root (cwd-independent).

    MCP servers and tools often start with a working directory that is not the
    repository root. Bare ``load_dotenv()`` / Dynaconf ``load_dotenv=True`` only
    search from cwd, so ``GITHUB_IGNORE_PATTERNS`` and tokens never apply.

    Returns the path loaded, or ``None`` if no project ``.env`` exists.
    """
    env_path = project_root() / ".env"
    if not env_path.is_file():
        return None
    load_dotenv(env_path, override=override)
    return env_path


class SettingsService(SettingsServiceInterface):
    """Settings service for reading TOML configuration files with Dynaconf.

    Uses manual caching with RLock for thread-safe operation
    because Dynaconf objects are unhashable (no @lru_cache).
    """

    def __init__(
        self,
        settings_files: list[str] | None = None,
    ) -> None:
        # Always load project-root .env before Dynaconf so os.getenv-based
        # overrides (GITHUB_IGNORE_PATTERNS, MAX_FILES_ALLOWED, …) work even
        # when the process cwd is not the repository root.
        load_project_dotenv(override=False)

        if settings_files is None:
            root = project_root()
            cwd_toml = Path.cwd() / "settings.toml"
            if cwd_toml.is_file():
                settings_files = ["settings.toml", ".secrets.toml"]
            else:
                # Wheels carry the canonical TOML as package data; editable
                # installs and checkouts still read the single root source.
                defaults = files("prdiffer").joinpath("settings.toml")
                settings_files = [
                    str(defaults) if defaults.is_file() else str(root / "settings.toml"),
                    str(root / ".secrets.toml"),
                ]

        self.settings = Dynaconf(
            settings_files=settings_files,
            environments=True,
            env_switcher="ENV_FOR_DYNACONF",
            load_dotenv=True,
        )

        self._cache_lock = RLock()
        self._github_config_cache: GitHubConfig | None = None
        self._gitlab_config_cache: GitLabConfig | None = None
        self._cache_settings_cache: dict[str, Any] | None = None
        self._app_settings_cache: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def get_str(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        if isinstance(value, str):
            return value
        return str(value) if value is not None else default

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        try:
            return int(value) if value is not None else default
        except ValueError, TypeError:
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value) if value is not None else default

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, default)
        if isinstance(value, float):
            return value
        try:
            return float(value) if value is not None else default
        except ValueError, TypeError:
            return default

    def get_github_config(self) -> GitHubConfig:
        """Get centralized GitHub configuration as a GitHubConfig dataclass.

        Services should prefer receiving a GitHubConfig object
        instead of individual parameters.
        """
        with self._cache_lock:
            if self._github_config_cache is not None:
                return self._github_config_cache

            def get_with_fallback(key: str, default: Any = None) -> Any:
                value = self.get(key)
                if value is None and hasattr(self.settings, "from_env"):
                    default_settings = self.settings.from_env("default")
                    value = default_settings.get(key, default) if default_settings else None
                return value if value is not None else default

            # Resolve from default Dynaconf environment with env overrides via Dynaconf.
            self._github_config_cache = GitHubConfig(
                rate_limit=int(get_with_fallback("github.rate_limit", 5000)),
                timeout=int(get_with_fallback("github.timeout", 30)),
                max_retries=int(get_with_fallback("github.max_retries", 3)),
                retry_delay=float(get_with_fallback("github.retry_delay", 1.0)),
                retry_on_404=bool(get_with_fallback("github.retry_on_404", False)),
                retry_on_403=bool(get_with_fallback("github.retry_on_403", True)),
                retry_on_500=bool(get_with_fallback("github.retry_on_500", True)),
                retry_log_level=str(get_with_fallback("github.retry_log_level", "DEBUG")),
                permanent_failure_log_level=str(get_with_fallback("github.permanent_failure_log_level", "INFO")),
                circuit_breaker_enabled=bool(get_with_fallback("github.circuit_breaker_enabled", True)),
                circuit_breaker_failure_threshold=int(get_with_fallback("github.circuit_breaker_failure_threshold", 5)),
                circuit_breaker_timeout=int(get_with_fallback("github.circuit_breaker_timeout", 60)),
                adaptive_retry_enabled=bool(get_with_fallback("github.adaptive_retry_enabled", True)),
                max_adaptive_delay=int(get_with_fallback("github.max_adaptive_delay", 30)),
                api_health_tracking=bool(get_with_fallback("github.api_health_tracking", True)),
                context_aware_retry=bool(get_with_fallback("github.context_aware_retry", True)),
                ignore_patterns=self._resolve_ignore_patterns(get_with_fallback),
                valid_extensions=tuple(get_with_fallback("github.valid_extensions", [])),
                diff_parallel_threshold=int(get_with_fallback("github.diff_parallel_threshold", 3)),
                diff_max_workers=int(get_with_fallback("github.diff_max_workers", 4)),
                max_files_allowed=self._resolve_max_files_allowed(get_with_fallback),
                large_file_threshold=int(get_with_fallback("diff.large_file_threshold", 5000)),
                chunk_size=int(get_with_fallback("diff.chunk_size", 1000)),
                max_diff_size=int(get_with_fallback("diff.max_diff_size", 100000)),
                max_file_size_bytes=int(get_with_fallback("github.max_file_size_bytes", 10_485_760)),
                parallel_file_fetch_enabled=bool(get_with_fallback("performance.parallel_file_fetch_enabled", True)),
                parallel_diff_generation_enabled=bool(get_with_fallback("performance.parallel_diff_generation_enabled", True)),
                pr_diff_request_timeout_seconds=float(get_with_fallback("mcp.pr_diff_request_timeout_seconds", 180.0)),
                max_concurrent=int(get_with_fallback("github.max_concurrent", 4)),
            )
            return self._github_config_cache

    def get_gitlab_config(self) -> GitLabConfig:
        """Get centralized GitLab configuration as a GitLabConfig dataclass.

        Resolves gitlab.* keys with fallbacks to shared app/diff/mcp limits.
        """
        with self._cache_lock:
            if self._gitlab_config_cache is not None:
                return self._gitlab_config_cache

            def get_with_fallback(key: str, default: Any = None) -> Any:
                value = self.get(key)
                if value is None and hasattr(self.settings, "from_env"):
                    default_settings = self.settings.from_env("default")
                    value = default_settings.get(key, default) if default_settings else None
                return value if value is not None else default

            # max_files_allowed priority:
            # 1) MAX_FILES_ALLOWED env — works with start-prdiffer-mcp-server.sh /.env
            # 2) gitlab.max_files_allowed → app.max_files_allowed → 50
            max_files = self._resolve_max_files_allowed(
                get_with_fallback,
                gitlab_key="gitlab.max_files_allowed",
            )

            request_timeout = get_with_fallback("gitlab.pr_diff_request_timeout_seconds", None)
            if request_timeout is None:
                request_timeout = get_with_fallback("mcp.pr_diff_request_timeout_seconds", 180.0)

            # Host allowlist priority:
            # 1) GITLAB_ALLOWED_HOSTS env (CSV) — works with start-prdiffer-mcp-server.sh /.env
            # 2) settings.toml gitlab.allowed_hosts (list/CSV)
            # 3) GitLabConfig default ("gitlab.com",)
            env_hosts = os.getenv("GITLAB_ALLOWED_HOSTS")
            if env_hosts is not None and env_hosts.strip():
                allowed_hosts_raw: Any = env_hosts
            else:
                allowed_hosts_raw = get_with_fallback("gitlab.allowed_hosts", None)
            hosts_cfg = GitLabConfig.from_dict({"allowed_hosts": allowed_hosts_raw})

            self._gitlab_config_cache = GitLabConfig(
                timeout=int(get_with_fallback("gitlab.timeout", 30)),
                max_retries=int(get_with_fallback("gitlab.max_retries", 3)),
                max_concurrent=int(get_with_fallback("gitlab.max_concurrent", 4)),
                retry_transient_errors=bool(get_with_fallback("gitlab.retry_transient_errors", True)),
                obey_rate_limit=bool(get_with_fallback("gitlab.obey_rate_limit", True)),
                max_file_size_bytes=int(get_with_fallback("gitlab.max_file_size_bytes", 10_485_760)),
                max_files_allowed=int(max_files),
                pr_diff_request_timeout_seconds=float(request_timeout),
                allowed_hosts=hosts_cfg.allowed_hosts,
            )
            return self._gitlab_config_cache

    def get_cache_settings(self) -> dict[str, Any]:
        with self._cache_lock:
            if self._cache_settings_cache is not None:
                return self._cache_settings_cache

            self._cache_settings_cache = {
                "ttl": self.get("cache.ttl", 300),
                "max_size": self.get("cache.max_size", 1000),
                "enabled": self.get("cache.enabled", True),
            }
            return self._cache_settings_cache

    def get_app_settings(self) -> dict[str, Any]:
        with self._cache_lock:
            if self._app_settings_cache is not None:
                return self._app_settings_cache

            def get_with_fallback(key: str, default: Any = None) -> Any:
                value = self.get(key)
                if value is None and hasattr(self.settings, "from_env"):
                    default_settings = self.settings.from_env("default")
                    value = default_settings.get(key, default) if default_settings else None
                return value if value is not None else default

            self._app_settings_cache = {
                "debug": self.get("app.debug", False),
                "log_level": self.get("app.log_level", "INFO"),
                "max_files_allowed": self._resolve_max_files_allowed(get_with_fallback),
                "incremental_mode": self.get("app.incremental_mode", False),
                "logging_enabled": self.get("app.logging_enabled", True),
                "log_format": self.get("app.log_format", "simple"),
            }
            return self._app_settings_cache

    def get_configuration_warnings(self) -> list[str]:
        warnings: list[str] = []

        try:
            rate_limit = self.get("github.rate_limit", 5000)
            if rate_limit > 5000:
                warnings.append(f"High rate limit ({rate_limit}) may cause API throttling")

            timeout = self.get("github.timeout", 30)
            if timeout < 10:
                warnings.append(f"Low timeout ({timeout}s) may cause premature failures")

            max_retries = self.get("github.max_retries", 3)
            if max_retries > 10:
                warnings.append(f"High retry count ({max_retries}) may increase latency")

            github_token = os.getenv("GITHUB_TOKEN")
            if not github_token:
                warnings.append("GITHUB_TOKEN environment variable not set - using anonymous access")

            use_hashed_keys = self.get("cache.use_hashed_keys", True)
            if not use_hashed_keys:
                warnings.append("Cache key hashing disabled - may use more memory")

        except Exception as e:
            logger.error(
                "Error checking configuration settings",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            warnings.append(f"Error checking configuration: {e}")

        return warnings

    def is_development_mode(self) -> bool:
        return self.get("app.debug", False) or os.getenv("ENV_FOR_DYNACONF") == "development"

    @staticmethod
    def _resolve_max_files_allowed(
        get_with_fallback: Any,
        *,
        gitlab_key: str | None = None,
        default: int = 50,
    ) -> int:
        """Resolve selected-file admission limit.

        Priority:
        1) ``MAX_FILES_ALLOWED`` env (works with ``start-prdiffer-mcp-server.sh`` / ``.env``)
        2) optional provider key (e.g. ``gitlab.max_files_allowed``)
        3) ``app.max_files_allowed`` from settings.toml
        4) ``default`` (50)
        """
        env_val = os.getenv("MAX_FILES_ALLOWED")
        if env_val is not None and env_val.strip():
            return int(env_val.strip())

        if gitlab_key is not None:
            provider_val = get_with_fallback(gitlab_key, None)
            if provider_val is not None:
                return int(provider_val)

        return int(get_with_fallback("app.max_files_allowed", default))

    @staticmethod
    def _resolve_ignore_patterns(get_with_fallback: Any) -> tuple[str, ...]:
        """Resolve GitHub ignore file patterns.

        Priority:
        1) ``GITHUB_IGNORE_PATTERNS`` env (CSV) — works with start script / ``.env``
        2) ``github.ignore_patterns`` from settings.toml
        3) empty tuple

        When set, the env value **replaces** the toml list (does not append).
        Patterns support globs (``*.lock``, ``node_modules/``) and regex strings.
        """
        env_val = os.getenv("GITHUB_IGNORE_PATTERNS")
        if env_val is not None and env_val.strip():
            patterns = [part.strip() for part in env_val.split(",") if part.strip()]
            return tuple(patterns)

        raw = get_with_fallback("github.ignore_patterns", [])
        if raw is None:
            return ()
        if isinstance(raw, str):
            return tuple(part.strip() for part in raw.split(",") if part.strip())
        return tuple(str(p) for p in raw)

    def _get_loaded_config_files(self) -> list[str]:
        try:
            if hasattr(self.settings, "_loaded_files"):
                return list(self.settings._loaded_files)
            elif hasattr(self.settings, "settings_files"):
                return list(self.settings.settings_files)
            else:
                return []
        except AttributeError, TypeError, KeyError:
            return []

    def clear_cache(self) -> None:
        """Clear all cached settings in a thread-safe manner."""
        with self._cache_lock:
            self._github_config_cache = None
            self._gitlab_config_cache = None
            self._cache_settings_cache = None
            self._app_settings_cache = None


_settings_service: SettingsService | None = None


def get_settings_service() -> SettingsService:
    """Get or create the global settings service singleton."""
    global _settings_service
    if _settings_service is None:
        _settings_service = SettingsService()
    return _settings_service
