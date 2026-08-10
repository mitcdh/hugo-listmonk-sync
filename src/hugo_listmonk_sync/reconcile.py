"""Campaign reconciliation rules."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from hugo_listmonk_sync.errors import ListmonkError, SyncError
from hugo_listmonk_sync.feed import FeedClient, FeedPost
from hugo_listmonk_sync.listmonk import CampaignRef, ListmonkClient
from hugo_listmonk_sync.timestamps import parse_aware_iso8601

logger = logging.getLogger(__name__)

_DraftDecision = Literal["update", "up_to_date", "stale_feed_skipped"]


@dataclass(frozen=True, slots=True)
class CycleSummary:
    """Outcome counts for one reconciliation cycle."""

    created: int = 0
    updated: int = 0
    up_to_date: int = 0
    stale_feed_skipped: int = 0
    non_draft_skipped: int = 0
    ambiguous: int = 0
    failed: int = 0


class Synchronizer:
    """Reconcile a validated Hugo feed against Listmonk campaigns."""

    def __init__(
        self,
        feed_client: FeedClient,
        listmonk_client: ListmonkClient,
    ) -> None:
        self._feed = feed_client
        self._listmonk = listmonk_client

    def run_cycle(self) -> CycleSummary:
        """Run one full synchronization cycle."""
        posts = self._feed.fetch()
        campaigns = self._listmonk.list_campaigns()
        by_name = _index_campaigns(campaigns)

        counts = {
            "created": 0,
            "updated": 0,
            "up_to_date": 0,
            "stale_feed_skipped": 0,
            "non_draft_skipped": 0,
            "ambiguous": 0,
            "failed": 0,
        }
        for post in posts:
            matches = by_name.get(post.name, [])
            try:
                outcome = self._reconcile_post(post, matches)
            except SyncError:
                counts["failed"] += 1
                logger.exception("Failed to synchronize campaign %r", post.name)
            except Exception:
                counts["failed"] += 1
                logger.exception(
                    "Unexpected failure synchronizing campaign %r",
                    post.name,
                )
            else:
                counts[outcome] += 1

        summary = CycleSummary(**counts)
        logger.info(
            "Cycle summary: created=%d updated=%d up_to_date=%d "
            "stale_feed_skipped=%d non_draft_skipped=%d ambiguous=%d failed=%d",
            summary.created,
            summary.updated,
            summary.up_to_date,
            summary.stale_feed_skipped,
            summary.non_draft_skipped,
            summary.ambiguous,
            summary.failed,
        )
        return summary

    def _reconcile_post(
        self,
        post: FeedPost,
        matches: list[CampaignRef],
    ) -> str:
        if not matches:
            self._listmonk.create_campaign(post)
            logger.info("Created draft campaign %r", post.name)
            return "created"

        if len(matches) > 1:
            rendered = ", ".join(
                f"{campaign.id}:{campaign.status}" for campaign in matches
            )
            logger.error(
                "Ambiguous campaign name %r has multiple matches (%s); skipping",
                post.name,
                rendered,
            )
            return "ambiguous"

        match = matches[0]
        if match.status != "draft":
            logger.info(
                "Campaign %r (ID %d) is %s; leaving it unchanged",
                post.name,
                match.id,
                match.status,
            )
            return "non_draft_skipped"

        existing = self._listmonk.get_campaign(match.id)
        if existing.get("status") != "draft":
            logger.info(
                "Campaign %r (ID %d) changed to %s before update; leaving it unchanged",
                post.name,
                match.id,
                existing.get("status", "unknown"),
            )
            return "non_draft_skipped"
        if existing.get("name") != post.name:
            raise ListmonkError(
                f"Campaign ID {match.id} changed name during reconciliation"
            )

        decision = self._draft_decision(post, match, existing)
        if decision != "update":
            return decision

        self._listmonk.update_campaign(match.id, existing, post)
        logger.info("Updated draft campaign %r (ID %d)", post.name, match.id)
        return "updated"

    def _draft_decision(
        self,
        post: FeedPost,
        match: CampaignRef,
        existing: dict[str, object],
    ) -> _DraftDecision:
        if post.lastmod is None:
            return "update"

        stored = _stored_lastmod(existing)
        if stored.state == "malformed":
            logger.warning(
                "Campaign %r (ID %d) has malformed stored post.lastmod; "
                "updating conservatively",
                post.name,
                match.id,
            )
            return "update"
        if stored.state == "missing":
            logger.info(
                "Campaign %r (ID %d) has no stored post.lastmod; backfilling",
                post.name,
                match.id,
            )
            return "update"

        stored_value = stored.value
        if stored_value is None:
            raise AssertionError("present stored lastmod must have a value")
        if post.lastmod < stored_value:
            logger.warning(
                "Campaign %r (ID %d) has newer stored post.lastmod %s "
                "than feed lastmod %s; refusing rollback",
                post.name,
                match.id,
                stored_value.isoformat(),
                post.attributes.get("lastmod"),
            )
            return "stale_feed_skipped"
        if post.lastmod == stored_value and (
            self._listmonk.generated_content_is_current(existing, post)
        ):
            logger.info(
                "Draft campaign %r (ID %d) is up to date",
                post.name,
                match.id,
            )
            return "up_to_date"
        return "update"


def _index_campaigns(
    campaigns: tuple[CampaignRef, ...],
) -> dict[str, list[CampaignRef]]:
    by_name: dict[str, list[CampaignRef]] = defaultdict(list)
    for campaign in campaigns:
        by_name[campaign.name].append(campaign)
    return dict(by_name)


@dataclass(frozen=True, slots=True)
class _StoredLastmod:
    state: Literal["missing", "malformed", "present"]
    value: datetime | None = None


def _stored_lastmod(campaign: dict[str, object]) -> _StoredLastmod:
    attribs = campaign.get("attribs")
    if attribs is None:
        state: Literal["missing", "malformed"] = "missing"
    elif not isinstance(attribs, dict):
        state = "malformed"
    else:
        post = attribs.get("post")
        if post is None or (isinstance(post, dict) and "lastmod" not in post):
            state = "missing"
        elif not isinstance(post, dict):
            state = "malformed"
        else:
            value = post["lastmod"]
            if isinstance(value, str):
                try:
                    return _StoredLastmod("present", parse_aware_iso8601(value))
                except ValueError:
                    pass
            state = "malformed"
    return _StoredLastmod(state)
