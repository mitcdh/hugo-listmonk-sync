from __future__ import annotations

import logging

import pytest

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.errors import ConfigError


def test_parses_required_values_and_defaults(base_env):
    config = Config.from_env(base_env)

    assert config.newsletter_json_url == "https://hugo.example/newsletter.json"
    assert config.listmonk_base_url == "https://listmonk.example"
    assert config.listmonk_list_ids == (4, 9)
    assert config.campaign_name_field == "key"
    assert config.campaign_subject_field == "title"
    assert config.campaign_content_field == "html"
    assert config.listmonk_content_type == "html"
    assert config.listmonk_campaign_type == "regular"
    assert config.listmonk_messenger == "email"
    assert config.listmonk_template_id is None
    assert config.listmonk_from_email is None
    assert config.listmonk_campaign_tags == ()
    assert config.newsletter_header_kicker == "NEW BLOG POST"
    assert config.newsletter_author is None
    assert config.newsletter_address is None
    assert config.newsletter_site_name is None
    assert config.newsletter_base_url is None
    assert config.poll_interval_seconds == 3600
    assert config.http_timeout_seconds == 30
    assert config.http_max_retries == 3
    assert config.log_level == "INFO"
    assert config.numeric_log_level == logging.INFO
    assert config.run_once is False
    assert config.ignore_lastmod is False


def test_parses_all_optional_values(base_env):
    base_env.update(
        {
            "CAMPAIGN_NAME_FIELD": "slug",
            "CAMPAIGN_SUBJECT_FIELD": "heading",
            "CAMPAIGN_CONTENT_FIELD": "body",
            "LISTMONK_CONTENT_TYPE": "markdown",
            "LISTMONK_CAMPAIGN_TYPE": "optin",
            "LISTMONK_MESSENGER": "custom",
            "LISTMONK_TEMPLATE_ID": "8",
            "LISTMONK_FROM_EMAIL": " News <news@example.test> ",
            "LISTMONK_CAMPAIGN_TAGS": "hugo, newsletter",
            "NEWSLETTER_HEADER_KICKER": "LATEST ARTICLE",
            "NEWSLETTER_AUTHOR": " Publisher Name ",
            "NEWSLETTER_ADDRESS": " Postal address ",
            "NEWSLETTER_SITE_NAME": " Example Blog ",
            "NEWSLETTER_BASE_URL": "https://blog.example.test/news",
            "POLL_INTERVAL_SECONDS": "15",
            "HTTP_TIMEOUT_SECONDS": "2.5",
            "HTTP_MAX_RETRIES": "0",
            "LOG_LEVEL": "debug",
            "RUN_ONCE": "TRUE",
            "IGNORE_LASTMOD": "TRUE",
        }
    )

    config = Config.from_env(base_env)

    assert config.campaign_name_field == "slug"
    assert config.campaign_subject_field == "heading"
    assert config.campaign_content_field == "body"
    assert config.listmonk_content_type == "markdown"
    assert config.listmonk_campaign_type == "optin"
    assert config.listmonk_messenger == "custom"
    assert config.listmonk_template_id == 8
    assert config.listmonk_from_email == "News <news@example.test>"
    assert config.listmonk_campaign_tags == ("hugo", "newsletter")
    assert config.newsletter_header_kicker == "LATEST ARTICLE"
    assert config.newsletter_author == "Publisher Name"
    assert config.newsletter_address == "Postal address"
    assert config.newsletter_site_name == "Example Blog"
    assert config.newsletter_base_url == "https://blog.example.test/news"
    assert config.poll_interval_seconds == 15
    assert config.http_timeout_seconds == 2.5
    assert config.http_max_retries == 0
    assert config.log_level == "DEBUG"
    assert config.run_once is True
    assert config.ignore_lastmod is True


@pytest.mark.parametrize(
    "missing",
    [
        "NEWSLETTER_JSON_URL",
        "LISTMONK_BASE_URL",
        "LISTMONK_API_USERNAME",
        "LISTMONK_API_TOKEN",
        "LISTMONK_LIST_IDS",
    ],
)
def test_rejects_missing_required_values(base_env, missing):
    del base_env[missing]
    with pytest.raises(ConfigError, match=missing):
        Config.from_env(base_env)


@pytest.mark.parametrize(
    "value",
    ["", "1,", ",1", "1,two", "0", "-1", "2,2", "1.5"],
)
def test_rejects_malformed_list_ids(base_env, value):
    base_env["LISTMONK_LIST_IDS"] = value
    with pytest.raises(ConfigError, match="LISTMONK_LIST_IDS"):
        Config.from_env(base_env)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("POLL_INTERVAL_SECONDS", "0"),
        ("POLL_INTERVAL_SECONDS", "-2"),
        ("POLL_INTERVAL_SECONDS", "1.5"),
        ("HTTP_TIMEOUT_SECONDS", "0"),
        ("HTTP_TIMEOUT_SECONDS", "later"),
        ("HTTP_TIMEOUT_SECONDS", "nan"),
        ("HTTP_TIMEOUT_SECONDS", "inf"),
        ("HTTP_MAX_RETRIES", "-1"),
        ("HTTP_MAX_RETRIES", "1.5"),
        ("LISTMONK_TEMPLATE_ID", "0"),
        ("LISTMONK_TEMPLATE_ID", "first"),
    ],
)
def test_rejects_invalid_numeric_values(base_env, name, value):
    base_env[name] = value
    with pytest.raises(ConfigError, match=name):
        Config.from_env(base_env)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NEWSLETTER_JSON_URL", "/newsletter.json"),
        ("NEWSLETTER_JSON_URL", "file:///feed.json"),
        ("NEWSLETTER_JSON_URL", "https://user:pass@example.test/feed"),
        ("NEWSLETTER_BASE_URL", "/blog"),
        ("NEWSLETTER_BASE_URL", "file:///blog"),
        ("NEWSLETTER_BASE_URL", "https://user:pass@example.test/blog"),
        ("LISTMONK_BASE_URL", "listmonk.example"),
        ("LISTMONK_BASE_URL", "https://listmonk.example/api"),
        ("LISTMONK_BASE_URL", "https://listmonk.example?x=1"),
        ("LISTMONK_BASE_URL", "https://listmonk.example:not-a-port"),
        ("LISTMONK_BASE_URL", "https://listmonk.example:0"),
        ("LISTMONK_BASE_URL", "https://listmonk.example:99999"),
    ],
)
def test_rejects_invalid_urls(base_env, name, value):
    base_env[name] = value
    with pytest.raises(ConfigError, match=name):
        Config.from_env(base_env)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CAMPAIGN_NAME_FIELD", " "),
        ("CAMPAIGN_SUBJECT_FIELD", ""),
        ("CAMPAIGN_CONTENT_FIELD", " "),
        ("LISTMONK_MESSENGER", ""),
        ("NEWSLETTER_HEADER_KICKER", ""),
        ("LISTMONK_CAMPAIGN_TAGS", "one,,two"),
        ("LISTMONK_CONTENT_TYPE", "pdf"),
        ("LISTMONK_CAMPAIGN_TYPE", "transactional"),
        ("LOG_LEVEL", "TRACE"),
        ("RUN_ONCE", "yes"),
        ("IGNORE_LASTMOD", "yes"),
    ],
)
def test_rejects_invalid_option_values(base_env, name, value):
    base_env[name] = value
    with pytest.raises(ConfigError, match=name):
        Config.from_env(base_env)


def test_unset_or_blank_optional_request_values_are_omitted(base_env):
    base_env["LISTMONK_TEMPLATE_ID"] = " "
    base_env["LISTMONK_FROM_EMAIL"] = " "
    base_env["LISTMONK_CAMPAIGN_TAGS"] = " "
    base_env["NEWSLETTER_AUTHOR"] = " "
    base_env["NEWSLETTER_ADDRESS"] = " "
    base_env["NEWSLETTER_SITE_NAME"] = " "
    base_env["NEWSLETTER_BASE_URL"] = " "

    config = Config.from_env(base_env)

    assert config.listmonk_template_id is None
    assert config.listmonk_from_email is None
    assert config.listmonk_campaign_tags == ()
    assert config.newsletter_author is None
    assert config.newsletter_address is None
    assert config.newsletter_site_name is None
    assert config.newsletter_base_url is None


def test_ignore_lastmod_requires_one_shot_mode(base_env):
    base_env["IGNORE_LASTMOD"] = "true"

    with pytest.raises(ConfigError, match="requires RUN_ONCE=true"):
        Config.from_env(base_env)
