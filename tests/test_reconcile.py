from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from hugo_listmonk_sync.errors import FeedError, ListmonkError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.listmonk import CampaignRef
from hugo_listmonk_sync.reconcile import CycleSummary, Synchronizer


def make_post(name="key", subject="Title", lastmod=None) -> FeedPost:
    attributes = {"key": name, "title": subject}
    parsed_lastmod = None
    if lastmod is not None:
        attributes["lastmod"] = lastmod
        parsed_lastmod = datetime.fromisoformat(lastmod)
    return FeedPost(
        name=name,
        subject=subject,
        content=f"<p>{subject}</p>",
        attributes=attributes,
        html=f"<p>{subject}</p>",
        text=subject,
        lastmod=parsed_lastmod,
    )


@dataclass
class StubFeed:
    posts: tuple[FeedPost, ...] = ()
    error: Exception | None = None
    fetches: int = 0

    def fetch(self):
        self.fetches += 1
        if self.error:
            raise self.error
        return self.posts


@dataclass
class StubListmonk:
    campaigns: tuple[CampaignRef, ...] = ()
    full: dict[int, dict] = field(default_factory=dict)
    failures: dict[str, set[str]] = field(default_factory=dict)
    lists_called: int = 0
    gets: list[int] = field(default_factory=list)
    creates: list[FeedPost] = field(default_factory=list)
    updates: list[tuple[int, dict, FeedPost]] = field(default_factory=list)
    content_current: bool = True
    current_checks: list[tuple[dict, FeedPost]] = field(default_factory=list)

    def list_campaigns(self):
        self.lists_called += 1
        return self.campaigns

    def get_campaign(self, campaign_id):
        self.gets.append(campaign_id)
        campaign = self.full[campaign_id]
        if campaign["name"] in self.failures.get("get", set()):
            raise ListmonkError("get failed")
        return campaign

    def create_campaign(self, post):
        self.creates.append(post)
        if post.name in self.failures.get("create", set()):
            raise ListmonkError("create failed")
        return {"id": 100}

    def update_campaign(self, campaign_id, existing, post):
        self.updates.append((campaign_id, existing, post))
        if post.name in self.failures.get("update", set()):
            raise ListmonkError("update failed")
        return {"id": campaign_id}

    def generated_content_is_current(self, existing, post):
        self.current_checks.append((existing, post))
        return self.content_current


def sync(feed, listmonk, *, ignore_lastmod=False):
    return Synchronizer(feed, listmonk, ignore_lastmod=ignore_lastmod)


def test_missing_campaign_is_created():
    feed = StubFeed((make_post(),))
    listmonk = StubListmonk()

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(created=1)
    assert [item.name for item in listmonk.creates] == ["key"]
    assert listmonk.gets == []
    assert listmonk.updates == []


def test_one_draft_is_fetched_and_updated():
    feed = StubFeed((make_post(),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={7: {"id": 7, "name": "key", "status": "draft"}},
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(updated=1)
    assert listmonk.gets == [7]
    assert [item[0] for item in listmonk.updates] == [7]


def test_feed_without_lastmod_always_uses_previous_update_behavior():
    feed = StubFeed((make_post(),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {
                    "post": {"lastmod": "2099-01-01T00:00:00Z"},
                },
            }
        },
        content_current=True,
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(updated=1)
    assert len(listmonk.updates) == 1
    assert listmonk.current_checks == []


def test_missing_existing_lastmod_forces_backfill_update():
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:49Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {"post": {"date": "2026-08-09T07:34:55Z"}},
            }
        },
        content_current=True,
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(updated=1)
    assert len(listmonk.updates) == 1
    assert listmonk.current_checks == []


@pytest.mark.parametrize(
    "stored",
    [
        "not-a-timestamp",
        "2026-08-09T10:43:49",
        123,
    ],
)
def test_malformed_existing_lastmod_forces_conservative_update(stored):
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:49Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {"post": {"lastmod": stored}},
            }
        },
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(updated=1)
    assert len(listmonk.updates) == 1


def test_newer_feed_lastmod_updates_draft():
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:50Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {
                    "post": {"lastmod": "2026-08-09T10:43:49Z"},
                },
            }
        },
    )

    assert sync(feed, listmonk).run_cycle() == CycleSummary(updated=1)
    assert len(listmonk.updates) == 1


def test_equal_equivalent_offsets_and_current_content_are_up_to_date():
    feed = StubFeed((make_post(lastmod="2026-08-09T20:43:49+10:00"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {
                    "post": {"lastmod": "2026-08-09T10:43:49Z"},
                },
            }
        },
        content_current=True,
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(up_to_date=1)
    assert listmonk.updates == []
    assert len(listmonk.current_checks) == 1


def test_equal_timestamp_with_stale_generated_content_updates():
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:49Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {
                    "post": {"lastmod": "2026-08-09T10:43:49Z"},
                },
            }
        },
        content_current=False,
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(updated=1)
    assert len(listmonk.updates) == 1


def test_older_feed_lastmod_warns_and_never_updates(caplog):
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:48Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {
                    "post": {"lastmod": "2026-08-09T10:43:49Z"},
                },
            }
        },
        content_current=False,
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(stale_feed_skipped=1)
    assert listmonk.updates == []
    assert listmonk.current_checks == []
    assert "refusing rollback" in caplog.text


def test_ignore_lastmod_forces_older_feed_update_for_matching_draft(caplog):
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:48Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={
            7: {
                "id": 7,
                "name": "key",
                "status": "draft",
                "attribs": {
                    "post": {"lastmod": "2026-08-09T10:43:49Z"},
                },
            }
        },
        content_current=True,
    )

    summary = sync(feed, listmonk, ignore_lastmod=True).run_cycle()

    assert summary == CycleSummary(updated=1)
    assert len(listmonk.updates) == 1
    assert listmonk.current_checks == []
    assert "IGNORE_LASTMOD is enabled" in caplog.text


def test_ignore_lastmod_does_not_change_non_draft_campaign():
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:48Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "sent"),),
    )

    summary = sync(feed, listmonk, ignore_lastmod=True).run_cycle()

    assert summary == CycleSummary(non_draft_skipped=1)
    assert listmonk.gets == []
    assert listmonk.updates == []


def test_ignore_lastmod_does_not_bypass_draft_status_race():
    feed = StubFeed((make_post(lastmod="2026-08-09T10:43:48Z"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={7: {"id": 7, "name": "key", "status": "scheduled"}},
    )

    summary = sync(feed, listmonk, ignore_lastmod=True).run_cycle()

    assert summary == CycleSummary(non_draft_skipped=1)
    assert listmonk.updates == []


@pytest.mark.parametrize(
    "status",
    [
        "sent",
        "scheduled",
        "running",
        "paused",
        "cancelled",
        "finished",
    ],
)
def test_non_draft_campaigns_remain_unchanged(status):
    feed = StubFeed((make_post(),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", status),),
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(non_draft_skipped=1)
    assert listmonk.gets == []
    assert listmonk.creates == []
    assert listmonk.updates == []


def test_campaign_that_stops_being_draft_during_cycle_is_not_updated():
    feed = StubFeed((make_post(),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={7: {"id": 7, "name": "key", "status": "scheduled"}},
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(non_draft_skipped=1)
    assert listmonk.updates == []


@pytest.mark.parametrize(
    "campaigns",
    [
        (
            CampaignRef(1, "key", "draft"),
            CampaignRef(2, "key", "draft"),
        ),
        (
            CampaignRef(1, "key", "draft"),
            CampaignRef(2, "key", "finished"),
        ),
        (
            CampaignRef(1, "key", "sent"),
            CampaignRef(2, "key", "finished"),
        ),
    ],
)
def test_duplicate_existing_names_are_ambiguous_and_never_mutated(campaigns):
    feed = StubFeed((make_post(),))
    listmonk = StubListmonk(campaigns=campaigns)

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(ambiguous=1)
    assert listmonk.gets == []
    assert listmonk.creates == []
    assert listmonk.updates == []


def test_matching_is_exact_and_case_sensitive():
    feed = StubFeed((make_post("Exact"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(1, "exact", "draft"),),
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(created=1)
    assert [item.name for item in listmonk.creates] == ["Exact"]


def test_feed_entries_disappearing_does_not_delete_or_mutate_campaigns():
    feed = StubFeed(())
    listmonk = StubListmonk(
        campaigns=(CampaignRef(1, "old-key", "draft"),),
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary()
    assert listmonk.creates == []
    assert listmonk.updates == []


def test_feed_failure_happens_before_campaign_listing_or_mutation():
    feed = StubFeed(error=FeedError("invalid feed"))
    listmonk = StubListmonk()

    with pytest.raises(FeedError):
        sync(feed, listmonk).run_cycle()

    assert listmonk.lists_called == 0
    assert listmonk.creates == []
    assert listmonk.updates == []


def test_campaign_listing_failure_stops_cycle_before_mutation():
    class BrokenListmonk(StubListmonk):
        def list_campaigns(self):
            raise ListmonkError("authentication failed")

    feed = StubFeed((make_post(),))
    listmonk = BrokenListmonk()

    with pytest.raises(ListmonkError):
        sync(feed, listmonk).run_cycle()

    assert listmonk.creates == []


def test_per_entry_failures_are_isolated():
    feed = StubFeed(
        (
            make_post("create-fails"),
            make_post("update-fails"),
            make_post("created"),
            make_post("skipped"),
            make_post("ambiguous"),
        )
    )
    listmonk = StubListmonk(
        campaigns=(
            CampaignRef(2, "update-fails", "draft"),
            CampaignRef(4, "skipped", "finished"),
            CampaignRef(5, "ambiguous", "draft"),
            CampaignRef(6, "ambiguous", "sent"),
        ),
        full={
            2: {
                "id": 2,
                "name": "update-fails",
                "status": "draft",
            }
        },
        failures={
            "create": {"create-fails"},
            "update": {"update-fails"},
        },
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(
        created=1,
        non_draft_skipped=1,
        ambiguous=1,
        failed=2,
    )
    assert [item.name for item in listmonk.creates] == [
        "create-fails",
        "created",
    ]


def test_name_change_between_list_and_full_get_is_failed_not_overwritten():
    feed = StubFeed((make_post("key"),))
    listmonk = StubListmonk(
        campaigns=(CampaignRef(7, "key", "draft"),),
        full={7: {"id": 7, "name": "other", "status": "draft"}},
    )

    summary = sync(feed, listmonk).run_cycle()

    assert summary == CycleSummary(failed=1)
    assert listmonk.updates == []
