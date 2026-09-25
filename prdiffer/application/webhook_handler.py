"""Webhook handling for GitHub cache invalidation via FastMCP."""

import hmac
import json
from collections.abc import Callable, Awaitable
from typing import Any


from starlette.requests import Request
from starlette.responses import JSONResponse

from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol


class WebhookHandler:
    """Processes GitHub webhook events and invalidates cache when PRs or repositories are updated."""

    def __init__(
        self,
        settings_service: SettingsServiceInterface,
        cache_service: CacheServiceInterface,
        repository_cache_service: RepositoryCacheServiceInterface,
        logger: LoggerServiceInterface,
        input_validator: InputValidatorProtocol,
    ):
        self._settings_service = settings_service
        self._cache_service = cache_service
        self._repository_cache_service = repository_cache_service
        self._logger = logger
        self._input_validator = input_validator

    async def webhook_invalidate_cache(self, payload_bytes: bytes, signature: str, github_event: str) -> dict[str, Any]:
        """Handle webhook events for cache invalidation with HMAC verification.

        Args:
            payload_bytes: Raw webhook payload bytes from GitHub
            signature: HMAC signature header value (X-Hub-Signature-256)
            github_event: GitHub event type (push, pull_request, etc.)

        Returns:
            Response dict indicating success or failure.

        Raises:
            ValueError: If signature verification fails or payload is invalid
        """
        webhook_secret = self._settings_service.get("github.webhook.secret", default="")

        if not webhook_secret:
            self._logger.warning(
                "Webhook received but no secret configured",
                github_event=github_event,
            )
            return {"status": "error", "message": "Webhook secret not configured"}

        if github_event not in ["pull_request", "push"]:
            self._logger.warning(
                "Unsupported webhook event type",
                github_event=github_event,
            )
            return {"status": "error", "message": "Unsupported event type"}

        expected_signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        if not hmac.compare_digest(expected_signature.encode(), signature.encode()):
            self._logger.warning(
                "Invalid webhook signature",
                github_event=github_event,
            )
            return {"status": "error", "message": "Invalid signature"}

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._logger.error(
                "Failed to parse webhook payload after HMAC verification",
                github_event=github_event,
                error=str(e),
            )
            return {"status": "error", "message": "Invalid payload format"}

        repository = payload.get("repository") if isinstance(payload, dict) else None
        repository_full_name = repository.get("full_name") if isinstance(repository, dict) else None
        repository_parts = repository_full_name.split("/") if isinstance(repository_full_name, str) else []
        if (
            len(repository_parts) != 2
            or any(not part or any(char.isspace() for char in part) for part in repository_parts)
        ):
            self._logger.warning(
                "Webhook payload has invalid repository information",
                github_event=github_event,
            )
            return {"status": "error", "message": "Missing repository info"}

        owner, repo = repository_parts
        cache_key = None
        if github_event == "pull_request":
            action = payload.get("action")
            if action in ["opened", "synchronize", "reopened"]:
                number = payload.get("number")
                if type(number) is not int or number <= 0:
                    self._logger.warning("Webhook payload has invalid PR number", github_event=github_event)
                    return {"status": "error", "message": "Invalid PR number"}
                cache_key = f"{repository_full_name}/pr/{number}"
                self._logger.info(
                    "Invalidating cache on PR updated",
                    cache_key=cache_key,
                    github_event=github_event,
                )
                await self._cache_service.invalidate_github_pr(owner, repo, number)
                self._repository_cache_service.invalidate_github_pr(owner, repo, number)
        elif github_event == "push":
            cache_key = repository_full_name
            self._logger.info(
                "Invalidating cache on push",
                cache_key=cache_key,
                github_event=github_event,
            )
            await self._cache_service.invalidate_github_repository(owner, repo)
            self._repository_cache_service.invalidate_github_repository(owner, repo)

        self._logger.info(
            "Webhook processed successfully",
            github_event=github_event,
            cache_key=cache_key if cache_key else "N/A",
        )

        return {"status": "success", "message": "Cache invalidated"}

    def get_webhook_handler(self) -> Callable[[Request], Awaitable[JSONResponse]]:
        """Return the webhook handler function for FastMCP registration."""

        async def webhook_handler(request: Request) -> JSONResponse:
            """Handle GitHub webhook events for cache invalidation."""
            try:
                signature = request.headers.get("X-Hub-Signature-256", "")
                if not signature:
                    signature = request.headers.get("X-Hub-Signature", "")

                github_event = request.headers.get("X-GitHub-Event", "")

                payload_bytes = await request.body()

                result = await self.webhook_invalidate_cache(payload_bytes, signature, github_event)

                if result["status"] == "error":
                    error_message = result.get("message", "")
                    if error_message in ["Invalid payload format", "Invalid JSON payload"]:
                        return JSONResponse(result, status_code=400)
                    if error_message == "Invalid signature":
                        return JSONResponse(result, status_code=401)
                    return JSONResponse(result, status_code=400)

                return JSONResponse(result, status_code=200)
            except json.JSONDecodeError as e:
                self._logger.error(
                    "Failed to parse webhook payload",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return JSONResponse(
                    {"status": "error", "message": "Invalid JSON payload"},
                    status_code=400,
                )
            except Exception as e:
                self._logger.error(
                    "Webhook handler error",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return JSONResponse(
                    {"status": "error", "message": "Internal server error"},
                    status_code=500,
                )

        return webhook_handler
