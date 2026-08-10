"""Environment-only application configuration."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from hugo_listmonk_sync.errors import ConfigError

_CONTENT_TYPES = frozenset({"richtext", "html", "markdown", "plain", "visual"})
_CAMPAIGN_TYPES = frozenset({"regular", "optin"})
_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


@dataclass(frozen=True, slots=True)
class Config:
    """Validated service configuration."""

    newsletter_json_url: str
    listmonk_base_url: str
    listmonk_api_username: str
    listmonk_api_token: str
    listmonk_list_ids: tuple[int, ...]
    campaign_name_field: str = "key"
    campaign_subject_field: str = "title"
    campaign_content_field: str = "html"
    listmonk_content_type: str = "html"
    listmonk_campaign_type: str = "regular"
    listmonk_messenger: str = "email"
    listmonk_template_id: int | None = None
    listmonk_from_email: str | None = None
    listmonk_campaign_tags: tuple[str, ...] = ()
    newsletter_header_kicker: str = "NEW BLOG POST"
    newsletter_author: str | None = None
    newsletter_address: str | None = None
    newsletter_site_name: str | None = None
    newsletter_base_url: str | None = None
    poll_interval_seconds: int = 3600
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3
    log_level: str = "INFO"
    run_once: bool = False

    @property
    def numeric_log_level(self) -> int:
        """Return the logging module level represented by ``log_level``."""
        return _LOG_LEVELS[self.log_level]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        """Parse and validate configuration from an environment mapping."""
        newsletter_url = _required(env, "NEWSLETTER_JSON_URL")
        base_url = _required(env, "LISTMONK_BASE_URL")
        username = _required(env, "LISTMONK_API_USERNAME")
        token = _required(env, "LISTMONK_API_TOKEN")
        list_ids = _parse_positive_int_list(
            _required(env, "LISTMONK_LIST_IDS"), "LISTMONK_LIST_IDS"
        )

        _validate_url(newsletter_url, "NEWSLETTER_JSON_URL", origin_only=False)
        _validate_url(base_url, "LISTMONK_BASE_URL", origin_only=True)

        name_field = _nonempty(env, "CAMPAIGN_NAME_FIELD", "key")
        subject_field = _nonempty(env, "CAMPAIGN_SUBJECT_FIELD", "title")
        content_field = _nonempty(env, "CAMPAIGN_CONTENT_FIELD", "html")
        content_type = _choice(
            env,
            "LISTMONK_CONTENT_TYPE",
            "html",
            _CONTENT_TYPES,
        )
        campaign_type = _choice(
            env,
            "LISTMONK_CAMPAIGN_TYPE",
            "regular",
            _CAMPAIGN_TYPES,
        )
        messenger = _nonempty(env, "LISTMONK_MESSENGER", "email")

        template_id = _optional_positive_int(env, "LISTMONK_TEMPLATE_ID")
        from_email = _optional_nonempty(env, "LISTMONK_FROM_EMAIL")
        tags = _parse_optional_csv(env.get("LISTMONK_CAMPAIGN_TAGS"), "tags")
        header_kicker = _nonempty(
            env,
            "NEWSLETTER_HEADER_KICKER",
            "NEW BLOG POST",
        )
        author = _optional_nonempty(env, "NEWSLETTER_AUTHOR")
        address = _optional_nonempty(env, "NEWSLETTER_ADDRESS")
        site_name = _optional_nonempty(env, "NEWSLETTER_SITE_NAME")
        newsletter_base_url = _optional_nonempty(env, "NEWSLETTER_BASE_URL")
        if newsletter_base_url is not None:
            _validate_url(
                newsletter_base_url,
                "NEWSLETTER_BASE_URL",
                origin_only=False,
            )
        poll_interval = _positive_int(env, "POLL_INTERVAL_SECONDS", 3600)
        timeout = _positive_float(env, "HTTP_TIMEOUT_SECONDS", 30.0)
        max_retries = _nonnegative_int(env, "HTTP_MAX_RETRIES", 3)
        log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _LOG_LEVELS:
            choices = ", ".join(_LOG_LEVELS)
            raise ConfigError(f"LOG_LEVEL must be one of: {choices}")
        run_once = _boolean(env, "RUN_ONCE", False)

        return cls(
            newsletter_json_url=newsletter_url,
            listmonk_base_url=base_url.rstrip("/"),
            listmonk_api_username=username,
            listmonk_api_token=token,
            listmonk_list_ids=list_ids,
            campaign_name_field=name_field,
            campaign_subject_field=subject_field,
            campaign_content_field=content_field,
            listmonk_content_type=content_type,
            listmonk_campaign_type=campaign_type,
            listmonk_messenger=messenger,
            listmonk_template_id=template_id,
            listmonk_from_email=from_email,
            listmonk_campaign_tags=tags,
            newsletter_header_kicker=header_kicker,
            newsletter_author=author,
            newsletter_address=address,
            newsletter_site_name=site_name,
            newsletter_base_url=newsletter_base_url,
            poll_interval_seconds=poll_interval,
            http_timeout_seconds=timeout,
            http_max_retries=max_retries,
            log_level=log_level,
            run_once=run_once,
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise ConfigError(f"{name} is required and must not be empty")
    return value.strip()


def _nonempty(
    env: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    value = env.get(name, default).strip()
    if not value:
        raise ConfigError(f"{name} must not be empty")
    return value


def _optional_nonempty(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    parsed = value.strip()
    if not parsed:
        return None
    return parsed


def _choice(
    env: Mapping[str, str],
    name: str,
    default: str,
    choices: frozenset[str],
) -> str:
    value = _nonempty(env, name, default)
    if value not in choices:
        rendered = ", ".join(sorted(choices))
        raise ConfigError(f"{name} must be one of: {rendered}")
    return value


def _parse_positive_int_list(value: str, name: str) -> tuple[int, ...]:
    parts = value.split(",")
    parsed: list[int] = []
    for part in parts:
        candidate = part.strip()
        if not candidate:
            raise ConfigError(
                f"{name} must be a comma-separated list of positive integers"
            )
        try:
            number = int(candidate)
        except ValueError as exc:
            raise ConfigError(
                f"{name} must be a comma-separated list of positive integers"
            ) from exc
        if number <= 0:
            raise ConfigError(f"{name} values must be positive integers")
        if number in parsed:
            raise ConfigError(f"{name} must not contain duplicate list IDs")
        parsed.append(number)
    return tuple(parsed)


def _parse_optional_csv(value: str | None, label: str) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()
    parsed = tuple(item.strip() for item in value.split(","))
    if any(not item for item in parsed):
        raise ConfigError(
            f"LISTMONK_CAMPAIGN_TAGS must be comma-separated non-empty {label}"
        )
    return parsed


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name, str(default)).strip()
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if number <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return number


def _optional_positive_int(env: Mapping[str, str], name: str) -> int | None:
    value = env.get(name)
    if value is None or not value.strip():
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer when set") from exc
    if number <= 0:
        raise ConfigError(f"{name} must be a positive integer when set")
    return number


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    value = env.get(name, str(default)).strip()
    try:
        number = float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ConfigError(f"{name} must be a positive number")
    return number


def _nonnegative_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name, str(default)).strip()
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a non-negative integer") from exc
    if number < 0:
        raise ConfigError(f"{name} must be a non-negative integer")
    return number


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, str(default).lower()).strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigError(f"{name} must be either true or false")


def _validate_url(value: str, name: str, *, origin_only: bool) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{name} must be an absolute HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port == 0
    ):
        raise ConfigError(f"{name} must be an absolute HTTP(S) URL")
    if origin_only and (
        parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise ConfigError(f"{name} must be an origin without a path or /api")
