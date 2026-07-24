from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from hugo_listmonk_sync.errors import FeedError, ListmonkError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.listmonk import CampaignRef
from hugo_listmonk_sync.reconcile import CycleSummary, Synchronizer


def make_post(name="key", subject="Title") -> FeedPost:
    return FeedPost(
        name=name,
        subject=subject,
        content=f"<p>{subject}</p>",
        attributes={"key": name, "title": subject},
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


def sync(feed, listmonk):
    return Synchronizer(feed, listmonk)


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
