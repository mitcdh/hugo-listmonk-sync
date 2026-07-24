"""Console entry point."""

from __future__ import annotations

import logging
import os
import threading

import httpx

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.errors import ConfigError
from hugo_listmonk_sync.feed import FeedClient
from hugo_listmonk_sync.http import RetryingHttpClient, RetryPolicy
from hugo_listmonk_sync.listmonk import ListmonkClient
from hugo_listmonk_sync.loop import ServiceLoop, install_signal_handlers
from hugo_listmonk_sync.reconcile import Synchronizer

logger = logging.getLogger(__name__)
_USER_AGENT = "hugo-listmonk-sync/1.0"


def main() -> int:
    """Load configuration and run the service."""
    try:
        config = Config.from_env(os.environ)
    except ConfigError as exc:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logger.error("Invalid configuration: %s", exc)
        return 2

    logging.basicConfig(
        level=config.numeric_log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    policy = RetryPolicy(max_retries=config.http_max_retries)
    timeout = httpx.Timeout(config.http_timeout_seconds)
    common_headers = {"User-Agent": _USER_AGENT}
    stop_event = threading.Event()

    with (
        httpx.Client(
            timeout=timeout,
            headers=common_headers,
            follow_redirects=True,
            trust_env=True,
        ) as feed_httpx,
        httpx.Client(
            timeout=timeout,
            headers=common_headers,
            auth=httpx.BasicAuth(
                config.listmonk_api_username,
                config.listmonk_api_token,
            ),
            follow_redirects=True,
            trust_env=True,
        ) as listmonk_httpx,
    ):
        feed = FeedClient(
            RetryingHttpClient(feed_httpx, policy),
            url=config.newsletter_json_url,
            name_field=config.campaign_name_field,
            subject_field=config.campaign_subject_field,
            content_field=config.campaign_content_field,
        )
        listmonk = ListmonkClient(
            RetryingHttpClient(listmonk_httpx, policy),
            config,
        )
        service = ServiceLoop(
            config,
            Synchronizer(feed, listmonk),
            stop_event=stop_event,
        )
        restore_handlers = install_signal_handlers(stop_event)
        try:
            service.run()
        finally:
            restore_handlers()
    return 0
