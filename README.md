# hugo-listmonk-sync

`hugo-listmonk-sync` creates Listmonk draft campaigns from Hugo blog posts.

Hugo publishes a small JSON feed at `/newsletter.json`. This service checks
that feed, creates a draft for each new post, and refreshes an existing draft
when the post changes. You still review, test, schedule, and send the campaign
from Listmonk.

The service is intentionally cautious:

- It only creates and updates drafts.
- It never sends, schedules, archives, deletes, or recreates campaigns.
- It never changes a campaign that has already left draft status.
- It keeps existing Listmonk settings such as lists, sender, template, and
  tags.

[Listmonk 6.0 or newer](https://github.com/knadh/listmonk/releases/tag/v6.0.0)
is required.

In normal use:

1. Mark a Hugo post with `newsletter: true`.
2. Build and publish the Hugo site.
3. The service creates or updates the matching Listmonk draft.
4. Review and send the draft from Listmonk when it is ready.

## What you need

- Docker and Docker Compose
- A Hugo site that can publish the JSON feed described below
- A reachable Listmonk 6.0+ installation
- A dedicated Listmonk API user

This repository includes:

- a working Hugo feed example in
  [`examples/hugo-newsletter`](examples/hugo-newsletter/)
- a ready-to-customise Listmonk template in
  [`blog-updates.gohtml`](blog-updates.gohtml)
- the synchronization service in `src/hugo_listmonk_sync`

## Quick start

Clone the repository and create your environment file:

```sh
git clone https://github.com/mitcdh/hugo-listmonk-sync.git
cd hugo-listmonk-sync
cp .env.example .env
```

Open `.env` and set at least these values:

```dotenv
NEWSLETTER_JSON_URL=https://blog.example.com/newsletter.json
LISTMONK_BASE_URL=https://listmonk.example.com
LISTMONK_API_USERNAME=hugo-newsletter-sync
LISTMONK_API_TOKEN=replace-with-api-token
LISTMONK_LIST_IDS=1
```

`LISTMONK_LIST_IDS` must contain Listmonk's numeric list IDs. These are not the
public UUIDs used by subscription forms. Separate multiple IDs with commas.

Start the service:

```sh
docker compose up --build -d
docker compose logs -f hugo-listmonk-sync
```

The first check runs immediately. By default, the service checks again one
hour after each run finishes.

Open Listmonk and confirm that posts from the feed appear as draft campaigns.
Stop the service with:

```sh
docker compose down
```

## Set up Listmonk

Create a dedicated user in **Admin → Users**, generate an API token, and put
the username and token in `.env`.

Give the user these permissions:

- `campaigns:get`
- `campaigns:manage`

Its list role must cover the lists in `LISTMONK_LIST_IDS` and any lists already
attached to drafts that the service will update. You can instead use
`campaigns:get_all` and `campaigns:manage_all` if that is more suitable for
your installation.

The service does not need `campaigns:send` and never uses it.

`LISTMONK_BASE_URL` is the address of Listmonk itself, for example
`https://listmonk.example.com`. Do not add `/api` to the end.

## Set up the Hugo feed

The easiest starting point is the complete example in
[`examples/hugo-newsletter`](examples/hugo-newsletter/). Copy its newsletter
layout and supporting partials into your Hugo site, then add the output format
to your Hugo configuration.

### Choose which posts to include

The example includes posts whose front matter contains `newsletter: true`:

```yaml
---
title: "Post title"
date: 2025-01-01T12:00:00+10:00
lastmod: 2025-01-02T09:30:00+10:00
description: "A short description for the email."
image: "/images/my-post.webp"
newsletter: true
tags: [Writing, Technology]
---
```

The last part of the post URL becomes the campaign name. A post at
`/posts/my-post/` therefore creates a campaign named `my-post`.

`lastmod` should change whenever the post changes. The included example uses
the publication date when there is no later `lastmod`, so new posts work
without any extra setup.

### Add the newsletter output

Add this to your Hugo configuration:

```toml
baseURL = "https://blog.example.com/"

[outputFormats.Newsletter]
  mediaType = "application/json"
  baseName = "newsletter"
  isPlainText = true
  notAlternative = true

[outputs]
  home = ["HTML", "RSS", "JSON", "Newsletter"]
```

Use your real public URL for `baseURL`. Hugo will write the feed to
`public/newsletter.json`.

The example layout also makes article links and images absolute and protects
displayed `{{` and `}}` code from Listmonk's template engine. Copy all four
partials from
[`layouts/partials/newsletter`](examples/hugo-newsletter/layouts/partials/newsletter/),
not only the main JSON layout.

### Check the feed

Build the example from the repository root:

```sh
hugo --source examples/hugo-newsletter --gc --minify
jq '.posts[] | {key, title, date, lastmod, url}' \
  examples/hugo-newsletter/public/newsletter.json
```

After deploying your site, check the public copy:

```sh
curl --fail --silent --show-error https://blog.example.com/newsletter.json \
  | jq '.posts[] | {key, title, date, lastmod, url}'
```

## Feed format

The feed uses this shape:

```json
{
  "schemaVersion": 1,
  "posts": [
    {
      "key": "my-post",
      "url": "https://blog.example.com/posts/my-post/",
      "title": "My post",
      "description": "A short post description.",
      "date": "2025-01-01T12:00:00+10:00",
      "lastmod": "2025-01-02T09:30:00+10:00",
      "readingTime": 8,
      "image": "/images/my-post.webp",
      "tags": ["Writing", "Technology"],
      "html": "<p>Newsletter body</p>",
      "text": "Newsletter body"
    }
  ]
}
```

Each post needs these three non-empty fields:

- `key`: the campaign name and matching key
- `title`: the campaign subject
- `html`: the campaign body

The `CAMPAIGN_NAME_FIELD`, `CAMPAIGN_SUBJECT_FIELD`, and
`CAMPAIGN_CONTENT_FIELD` settings let you use different field names.

`date` is the publication date shown in the email. `lastmod` tells the service
whether a post has changed. Both should be ISO 8601 timestamps with a timezone,
such as `2025-01-02T09:30:00+10:00` or `2025-01-01T23:30:00Z`.

The `text` field is only a backup. The service normally creates the plain-text
email from `html` so both versions contain the same structure and links.

Other fields are copied into `.Campaign.Attribs.post`, where the Listmonk
template can use them. The large `html` and `text` fields are not copied there.

The whole feed is checked before Listmonk is changed. Bad JSON, duplicate
keys, missing required fields, or an invalid `lastmod` stop that run without
changing any campaign.

## How campaigns are matched and updated

Campaign names are matched exactly, including capital letters.

| What Listmonk contains | What the service does |
| --- | --- |
| No campaign with that name | Creates a new draft. |
| One draft with that name | Updates it if the feed or generated content changed. |
| One non-draft with that name | Leaves it alone. |
| More than one matching campaign | Reports an ambiguous match and changes none of them. |

For one matching draft:

- A newer feed `lastmod` updates the draft.
- An equal `lastmod` updates the draft only when generated content is
  different. Otherwise it is reported as `up_to_date`.
- An older feed `lastmod` is reported as `stale_feed_skipped`. Nothing is
  overwritten.
- A draft with no stored `lastmod` is updated once so the timestamp and
  generated plain text can be added.
- A malformed stored timestamp produces a warning and a cautious update.
- An older feed with no `lastmod` remains compatible, but its matching drafts
  are updated on every run.

The publication `date` is never used to decide whether a draft needs updating.

### What the service owns

When an update is allowed, the service replaces:

- campaign name and subject
- HTML body
- plain-text alternate body
- `.Campaign.Attribs.post`
- `.Campaign.Attribs.newsletter`

It keeps the campaign's lists, sender, Listmonk template, tags, messenger,
campaign type, content type, headers, scheduled time, and unrelated
attributes. Campaigns that are not in the feed are also left alone.

Listmonk status is checked again immediately before every update. If someone
sends or otherwise changes the draft at that moment, the service refuses to
update it.

## Generated email content

The service owns both the HTML body and the plain-text alternate body.

For HTML email it:

- keeps useful article content such as headings, lists, links, images, tables,
  blockquotes, footnotes, and code
- removes browser-only controls such as **Copy** and **Wrap**, hidden status
  text, scripts, and article-local styles
- expands `<details>` sections so their content is visible
- replaces `<iframe>` embeds with ordinary links
- makes relative links and image URLs absolute
- gives tables compact, padded columns that fit their content
- protects displayed `{{` and `}}` so Listmonk does not try to run article
  examples as template code

The plain-text email follows the same order as the supplied HTML template:
title, description, author/date/reading time, article, full-post link, and
footer links. It keeps readable headings, lists, blockquotes, code blocks, and
external link destinations.

Small tables become fixed-width plain-text columns without Markdown pipes. If
an aligned row would be longer than 80 characters, the table is replaced with
a note directing the reader to the full article. Images with useful alt text
include their description and URL. Decorative images are omitted.

Listmonk evaluates the plain-text body as a Go template for each recipient.
Post and newsletter metadata therefore use `.Campaign.Attribs` references,
while unsubscribe and browser-view links are generated per recipient. Braces
found in article text or code are displayed literally instead of being run as
template expressions.

If HTML-to-text conversion fails, the service uses the feed's `text` field. A
post with neither usable HTML nor fallback text is not changed.

## Email presentation and template

[`blog-updates.gohtml`](blog-updates.gohtml) is a complete responsive Listmonk
template. Create or edit a template in Listmonk, paste in this file, and change
the clearly marked **TEMPLATE SETTINGS** section at the top.

Set `LISTMONK_TEMPLATE_ID` to that template's numeric ID if new campaigns
should use it automatically. Existing drafts keep whichever Listmonk template
they already use.

`NEWSLETTER_SUBJECT_PREFIX` adds text only to the email subject. For example,
`NEWSLETTER_SUBJECT_PREFIX=[blog.mitcdh]` produces the subject
`[blog.mitcdh] Post title`. It does not change the title shown inside the HTML
or plain-text email. One separating space is added automatically.

These optional settings are shared by the generated plain text and the bundled
HTML template:

- `NEWSLETTER_HEADER_KICKER`
- `NEWSLETTER_AUTHOR`
- `NEWSLETTER_ADDRESS`
- `NEWSLETTER_SITE_NAME`
- `NEWSLETTER_BASE_URL`

A post can override them with the feed fields `headerKicker`, `author`,
`address`, `siteName`, and `baseURL`. Feed values win over environment values.
Missing values are left out rather than replaced with deployment-specific
text.

The template also has local settings for its logo, asset URL, fonts, colours,
date display, and optional **Read the full post** button.

## Configuration reference

Configuration comes from environment variables. `.env.example` contains a
complete example.

### Required settings

| Variable | Meaning |
| --- | --- |
| `NEWSLETTER_JSON_URL` | Public URL of the Hugo JSON feed. |
| `LISTMONK_BASE_URL` | Listmonk address without `/api`. |
| `LISTMONK_API_USERNAME` | Dedicated Listmonk API username. |
| `LISTMONK_API_TOKEN` | API user's token. |
| `LISTMONK_LIST_IDS` | Comma-separated numeric list IDs for new campaigns. |

### New campaign defaults

These settings are used when a campaign is created. They do not overwrite an
existing draft's Listmonk settings.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LISTMONK_CONTENT_TYPE` | `html` | Campaign content type. |
| `LISTMONK_CAMPAIGN_TYPE` | `regular` | `regular` or `optin`. |
| `LISTMONK_MESSENGER` | `email` | Listmonk messenger name. |
| `LISTMONK_TEMPLATE_ID` | unset | Numeric Listmonk template ID. |
| `LISTMONK_FROM_EMAIL` | unset | Sender, such as `News <news@example.com>`. |
| `LISTMONK_CAMPAIGN_TAGS` | unset | Comma-separated tags. |

### Feed field names

| Variable | Default | Meaning |
| --- | --- | --- |
| `CAMPAIGN_NAME_FIELD` | `key` | Feed field used as the campaign name. |
| `CAMPAIGN_SUBJECT_FIELD` | `title` | Feed field used as the subject. |
| `CAMPAIGN_CONTENT_FIELD` | `html` | Feed field used as the campaign body. |

### Email subject

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEWSLETTER_SUBJECT_PREFIX` | unset | Text placed before the email subject. One separating space is added automatically. |

### Newsletter presentation

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEWSLETTER_HEADER_KICKER` | `NEW BLOG POST` | Short heading above the title. |
| `NEWSLETTER_AUTHOR` | unset | Publisher or author name. |
| `NEWSLETTER_ADDRESS` | unset | Postal address. |
| `NEWSLETTER_SITE_NAME` | unset | Site name used in footer links. |
| `NEWSLETTER_BASE_URL` | unset | Public site URL. |

### Runtime settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `POLL_INTERVAL_SECONDS` | `3600` | Time between completed checks. |
| `HTTP_TIMEOUT_SECONDS` | `30` | Request timeout in seconds. |
| `HTTP_MAX_RETRIES` | `3` | Retries for safe requests after a temporary failure. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `RUN_ONCE` | `false` | Run one check and exit. |
| `IGNORE_LASTMOD` | `false` | Ignore timestamps for a one-off draft refresh. Requires `RUN_ONCE=true`. |

TLS certificates are always checked. If your Listmonk server uses a private
certificate authority, mount its CA bundle and set `SSL_CERT_FILE` or
`SSL_CERT_DIR` to its path inside the container.

## Force a one-off draft refresh

Normally, generated-content changes are applied automatically when timestamps
are equal. If you need to refresh all matching drafts regardless of their
stored timestamps, run one cycle with:

```sh
docker run --rm --env-file .env \
  --env RUN_ONCE=true \
  --env IGNORE_LASTMOD=true \
  ghcr.io/mitcdh/hugo-listmonk-sync:latest
```

For the example Podman Quadlet container:

```sh
podman exec \
  --env RUN_ONCE=true \
  --env IGNORE_LASTMOD=true \
  blog-listmonk-sync \
  /app/.venv/bin/hugo-listmonk-sync
```

This still only updates exact matching drafts. It does not change non-draft or
ambiguous campaigns. It can replace a stored timestamp that is newer than the
feed, so use it only for deliberate repairs and do not enable it permanently.

## Container image

Published AMD64 and ARM64 images are available at:

```text
ghcr.io/mitcdh/hugo-listmonk-sync:latest
```

Run the image without building the repository:

```sh
docker pull ghcr.io/mitcdh/hugo-listmonk-sync:latest
docker run --rm --env-file .env ghcr.io/mitcdh/hugo-listmonk-sync:latest
```

The image stores no credentials or application data and runs as UID/GID 10001.

## Common log messages

- `is up to date`: the draft already matches the feed.
- `is finished; leaving it unchanged`: the campaign is no longer a draft.
- `stale_feed_skipped`: Listmonk has a newer stored `lastmod`, so nothing was
  overwritten.
- `ambiguous`: more than one campaign has the exact same name, so none were
  changed.
- `failed=1`: one post failed, but the service continued with the remaining
  posts. The earlier error in the log explains why.

Each run ends with counts for created, updated, up-to-date, skipped, ambiguous,
and failed posts.

## Development

The project uses Python 3.13 and
[`uv`](https://docs.astral.sh/uv/):

```sh
uv sync --frozen --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

Build and smoke-test the container with:

```sh
docker build -t hugo-listmonk-sync:smoke .
./scripts/container-smoke.sh hugo-listmonk-sync:smoke
```
