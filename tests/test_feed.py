from __future__ import annotations

import pytest

from hugo_listmonk_sync.errors import FeedError
from hugo_listmonk_sync.feed import validate_feed


def valid_payload():
    return {
        "schemaVersion": 1,
        "posts": [
            {
                "key": "parsing-consensus",
                "url": "https://blog.example/posts/parsing-consensus/",
                "tags": ["Writing", "Nuclear"],
                "date": "2026-07-18T00:00:00+10:00",
                "description": "Experiments in parsing",
                "image": "",
                "readingTime": 12,
                "title": "Parsing Consensus",
                "html": "<p>Article</p>",
                "text": "Article",
            }
        ],
    }


def validate(payload):
    return validate_feed(
        payload,
        name_field="key",
        subject_field="title",
        content_field="html",
    )


def test_builds_exact_attributes_and_excludes_large_bodies():
    posts = validate(valid_payload())

    assert len(posts) == 1
    assert posts[0].name == "parsing-consensus"
    assert posts[0].subject == "Parsing Consensus"
    assert posts[0].content == "<p>Article</p>"
    assert posts[0].attributes == {
        "key": "parsing-consensus",
        "url": "https://blog.example/posts/parsing-consensus/",
        "tags": ["Writing", "Nuclear"],
        "date": "2026-07-18T00:00:00+10:00",
        "description": "Experiments in parsing",
        "image": "",
        "readingTime": "12 min read",
        "title": "Parsing Consensus",
    }


def test_preserves_string_reading_time_and_relative_image():
    payload = valid_payload()
    payload["posts"][0]["readingTime"] = "about twelve minutes"
    payload["posts"][0]["image"] = "/images/post.webp"

    post = validate(payload)[0]

    assert post.attributes["readingTime"] == "about twelve minutes"
    assert post.attributes["image"] == "/images/post.webp"


def test_formats_fractional_reading_time_without_extra_zeroes():
    payload = valid_payload()
    payload["posts"][0]["readingTime"] = 2.5

    assert validate(payload)[0].attributes["readingTime"] == "2.5 min read"


def test_boolean_reading_time_is_preserved_as_metadata():
    payload = valid_payload()
    payload["posts"][0]["readingTime"] = True

    assert validate(payload)[0].attributes["readingTime"] is True


def test_selectors_are_configurable_exact_top_level_keys():
    payload = {
        "schemaVersion": 1,
        "posts": [
            {
                "slug.value": "literal-dot-key",
                "heading": "Custom title",
                "markdown": "# Body",
            }
        ],
    }

    posts = validate_feed(
        payload,
        name_field="slug.value",
        subject_field="heading",
        content_field="markdown",
    )

    assert posts[0].name == "literal-dot-key"
    assert posts[0].subject == "Custom title"
    assert posts[0].content == "# Body"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"schemaVersion": 2, "posts": []},
        {"schemaVersion": "1", "posts": []},
        {"schemaVersion": 1},
        {"schemaVersion": 1, "posts": {}},
        {"schemaVersion": 1, "posts": ["not-an-object"]},
    ],
)
def test_rejects_invalid_feed_structure(payload):
    with pytest.raises(FeedError):
        validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", None),
        ("key", ""),
        ("key", " "),
        ("key", 123),
        ("title", []),
        ("html", {}),
    ],
)
def test_rejects_missing_or_non_string_selected_fields(field, value):
    payload = valid_payload()
    payload["posts"][0][field] = value

    with pytest.raises(FeedError, match=field):
        validate(payload)


def test_rejects_duplicate_primary_values_before_reconciliation():
    payload = valid_payload()
    duplicate = dict(payload["posts"][0])
    duplicate["title"] = "Different subject"
    payload["posts"].append(duplicate)

    with pytest.raises(FeedError, match="Duplicate.*parsing-consensus"):
        validate(payload)


def test_empty_posts_array_is_valid():
    assert validate({"schemaVersion": 1, "posts": []}) == ()


def test_attribute_data_is_deep_copied():
    payload = valid_payload()
    post = validate(payload)[0]
    payload["posts"][0]["tags"].append("Changed")

    assert post.attributes["tags"] == ["Writing", "Nuclear"]
