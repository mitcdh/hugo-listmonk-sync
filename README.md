# hugo-listmonk-sync

`hugo-listmonk-sync` turns selected Hugo posts into Listmonk draft campaigns.
It polls a versioned JSON feed, validates the complete response, and creates or
refreshes matching drafts without changing campaigns that have left draft
status.

The service is deliberately limited:

- It creates missing campaigns as drafts.
- It updates the subject, body, and post metadata of matching drafts.
- It never sends, schedules, archives, deletes, or recreates campaigns.
- It leaves sent and otherwise non-draft campaigns unchanged.

[Listmonk 6.0 or newer](https://github.com/knadh/listmonk/releases/tag/v6.0.0)
is required because the synchronizer stores Hugo metadata in campaign-level
`attribs`.

## How it works

```mermaid
flowchart LR
    Post["Hugo post<br><code>newsletter: true</code>"]
    Build["CI/CD or local<br>Hugo build"]
    Feed["<code>/newsletter.json</code><br>schema v1"]
    Sync["hugo-listmonk-sync<br>validate and reconcile"]
    Draft["Listmonk draft"]
    Send["Review, test,<br>schedule and send"]

    Post --> Build --> Feed --> Sync --> Draft --> Send
```

Hugo feed generation and draft reconciliation can be automated. Campaign
review and sending remain explicit Listmonk actions.

This repository includes both sides of the integration:

- [`examples/hugo-newsletter`](examples/hugo-newsletter/) is a self-contained
  Hugo feed implementation.
- [`blog-updates.gohtml`](blog-updates.gohtml) is a configurable Listmonk email
  template.
- `src/hugo_listmonk_sync` contains the polling and reconciliation service.

## Quick start

You need:

- Docker with Docker Compose
- a reachable Listmonk 6.0+ instance
- a published Hugo JSON feed that follows the [feed contract](#feed-contract)
- a dedicated Listmonk API user with the
  [required permissions](#listmonk-setup)

Clone the repository and create a local environment file:

```sh
git clone https://github.com/mitcdh/hugo-listmonk-sync.git
cd hugo-listmonk-sync
cp .env.example .env
```

Edit `.env` and replace the example URLs, token, and list ID:

```dotenv
NEWSLETTER_JSON_URL=https://blog.example.com/newsletter.json
LISTMONK_BASE_URL=https://listmonk.example.com
LISTMONK_API_USERNAME=hugo-newsletter-sync
LISTMONK_API_TOKEN=replace-with-api-token
LISTMONK_LIST_IDS=1
```

`LISTMONK_LIST_IDS` uses Listmonk's positive numeric API IDs, not the public
UUIDs used by subscription forms.

Build and start the service:

```sh
docker compose up --build -d
docker compose logs -f hugo-listmonk-sync
```

The first reconciliation runs immediately. By default, another cycle starts
one hour after the previous cycle finishes. Open Listmonk and confirm that each
unmatched feed entry appears as a draft campaign.

Stop the service with:

```sh
docker compose down
```

## Hugo feed setup

The included [`examples/hugo-newsletter`](examples/hugo-newsletter/) directory
is a runnable reference implementation. Copy its newsletter layout and
partials into your Hugo site, then merge the relevant settings from its
[`hugo.toml`](examples/hugo-newsletter/hugo.toml).

### Select posts

The example feed includes pages in the `posts` section whose front matter sets
`newsletter: true`:

```yaml
---
title: "Post title"
date: 2025-01-01T12:00:00+10:00
description: "A concise description used in the newsletter."
image: "/images/my-post.webp"
newsletter: true
tags: [Writing, Technology]
---
```

The final path component of `.RelPermalink` becomes the default campaign key.
For example, a post published at `/posts/my-post/` produces the key
`my-post`.

### Configure the output

Add a custom output format to your Hugo configuration:

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

The `Newsletter` output renders
[`layouts/home.newsletter.json`](examples/hugo-newsletter/layouts/home.newsletter.json)
to `public/newsletter.json`. `baseURL` must be the site's canonical public URL
so relative links and assets can be resolved for email clients.

Use the layout with all four supporting partials:

- [`absolute-urls.html`](examples/hugo-newsletter/layouts/partials/newsletter/absolute-urls.html)
  finds `href`, `src`, `srcset`, and `poster` attributes in rendered HTML.
- [`absolute-url.html`](examples/hugo-newsletter/layouts/partials/newsletter/absolute-url.html)
  resolves site-relative, page-relative, and page-resource URLs.
- [`absolute-srcset.html`](examples/hugo-newsletter/layouts/partials/newsletter/absolute-srcset.html)
  resolves each candidate without losing its width or density descriptor.
- [`escape-code-delimiters.html`](examples/hugo-newsletter/layouts/partials/newsletter/escape-code-delimiters.html)
  protects `{{` and `}}` inside displayed code from Listmonk's Go template
  renderer.

Rendered URLs inside the `html` field become absolute. The top-level `image`
field deliberately retains the front matter value so campaign templates can
handle it as metadata.

The example enables Goldmark's `unsafe` renderer only because its fixture
contains trusted raw HTML. A site that does not intentionally allow raw HTML in
Markdown does not need that setting.

### Build and verify

Build the standalone example from the repository root:

```sh
hugo --source examples/hugo-newsletter --gc --minify
jq -e '.schemaVersion == 1 and (.posts | type == "array")' \
  examples/hugo-newsletter/public/newsletter.json
jq '.posts[] | {key, title, url}' \
  examples/hugo-newsletter/public/newsletter.json
```

After deploying your Hugo site, verify the public feed:

```sh
curl --fail --silent --show-error https://blog.example.com/newsletter.json \
  | jq '.posts[] | {key, title, url}'
```

## Feed contract

The response must be a JSON object containing `schemaVersion: 1` and a `posts`
array:

```json
{
  "schemaVersion": 1,
  "posts": [
    {
      "key": "my-post",
      "url": "https://blog.example.com/posts/my-post/",
      "title": "My post",
      "description": "A concise post description.",
      "date": "2025-01-01T12:00:00+10:00",
      "readingTime": 8,
      "image": "/images/my-post.webp",
      "tags": ["Writing", "Technology"],
      "html": "<p>Newsletter body</p>",
      "text": "Newsletter body"
    }
  ]
}
```

By default, each post must have non-empty string values for:

- `key`, used as the Listmonk campaign name and synchronization key
- `title`, used as the campaign subject
- `html`, used as the campaign body

The `CAMPAIGN_*_FIELD` settings can select different top-level keys. Selectors
are literal keys, not JSONPath expressions. Every selected campaign name must
be unique within a cycle.

The synchronizer copies every top-level post field except `html` and `text`
into `.Campaign.Attribs.post`. Arbitrary additional metadata is preserved. A
numeric `readingTime` becomes `"<N> min read"`; an existing string is retained.

The complete feed is validated before any Listmonk mutation. Invalid JSON, an
unsupported schema version, a missing selected field, or duplicate campaign
names reject the entire cycle.

## Reconciliation and safety

Campaign names are matched exactly and case-sensitively:

| Matching campaigns | Action |
| --- | --- |
| None | Create a new draft. |
| One draft | Refresh that draft from the feed. |
| One non-draft | Leave it unchanged. |
| Two or more | Treat the name as ambiguous and change none of them. |

Draft updates replace only:

- campaign name
- subject
- body
- `attribs.post`

Existing lists, sender, template, tags, messenger, campaign and content types,
body source, alternate body, headers, scheduled time, and unrelated campaign
attributes are retained. Creation-only defaults never overwrite an existing
draft. Campaigns absent from the feed are left alone.

The service never calls Listmonk's send, status, archive, or delete endpoints.
It also rechecks a campaign's status immediately before updating it, avoiding
an update if the campaign left draft status during reconciliation.

After the feed passes validation, posts are reconciled independently. A
Listmonk error for one post is logged without preventing the remaining posts
from being processed, and each cycle ends with an outcome summary.

## Listmonk setup

Create a dedicated API user under **Admin → Users**. The service authenticates
with that user's username and generated token using HTTP Basic authentication.
See Listmonk's [API authentication](https://listmonk.app/docs/apis/apis/) and
[roles and permissions](https://listmonk.app/docs/roles-and-permissions/)
documentation.

Assign:

- `campaigns:get` and `campaigns:manage`, with a list role that covers every
  configured list and every list already attached to drafts the service may
  update; or
- the corresponding `campaigns:get_all` and `campaigns:manage_all`
  permissions.

Listmonk 6.1 separates `campaigns:send` from campaign management. This service
does not require or use that permission.

`LISTMONK_BASE_URL` must be the Listmonk origin, such as
`https://listmonk.example.com`. Do not include `/api` or another path.

## Configuration

Configuration is exclusively through environment variables. `.env.example`
contains every setting used by the example Compose service. Copy it to the
git-ignored `.env` file and never commit real credentials.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `NEWSLETTER_JSON_URL` | yes | — | Absolute HTTP(S) URL of the Hugo JSON feed. |
| `LISTMONK_BASE_URL` | yes | — | Listmonk origin without a path or `/api`. |
| `LISTMONK_API_USERNAME` | yes | — | Dedicated Listmonk API username. |
| `LISTMONK_API_TOKEN` | yes | — | API user's secret token. |
| `LISTMONK_LIST_IDS` | yes | — | Comma-separated, unique positive list IDs for new campaigns. |
| `CAMPAIGN_NAME_FIELD` | no | `key` | Post key used as campaign name and synchronization key. |
| `CAMPAIGN_SUBJECT_FIELD` | no | `title` | Post key used as campaign subject. |
| `CAMPAIGN_CONTENT_FIELD` | no | `html` | Post key used as campaign body. |
| `LISTMONK_CONTENT_TYPE` | no | `html` | New campaign content type: `richtext`, `html`, `markdown`, `plain`, or `visual`. |
| `LISTMONK_CAMPAIGN_TYPE` | no | `regular` | New campaign type: `regular` or `optin`. |
| `LISTMONK_MESSENGER` | no | `email` | New campaign messenger or configured custom messenger name. |
| `LISTMONK_TEMPLATE_ID` | no | unset | Positive template ID for new campaigns; omitted when unset. |
| `LISTMONK_FROM_EMAIL` | no | unset | Sender for new campaigns; omitted so Listmonk can use its default. |
| `LISTMONK_CAMPAIGN_TAGS` | no | unset | Comma-separated tags applied only when creating campaigns. |
| `POLL_INTERVAL_SECONDS` | no | `3600` | Wait after each completed cycle, in seconds. |
| `HTTP_TIMEOUT_SECONDS` | no | `30` | Positive request timeout; decimals are accepted. |
| `HTTP_MAX_RETRIES` | no | `3` | Retry count after the initial safe request. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `RUN_ONCE` | no | `false` | Run one immediate cycle and exit. |

Continuous mode waits only after a cycle completes, so slow cycles never
overlap. `RUN_ONCE=true` runs one cycle and exits, which is useful when an
external scheduler owns the cadence.

Feed GETs and Listmonk GET/PUTs retry HTTP 429 responses, transient 5xx
responses, and network failures with bounded exponential backoff and
`Retry-After` support. Campaign POSTs are attempted once because Listmonk does
not document an idempotency key. If a POST result is uncertain, the next cycle
discovers a successfully created campaign by name. A cycle-level failure is
logged and retried at the next configured interval.

TLS certificate verification is always enabled. For a private certificate
authority, mount its CA bundle into the container and set the standard
`SSL_CERT_FILE` or `SSL_CERT_DIR` environment variable to the in-container
path. There is no insecure TLS option.

## Campaign template

Listmonk templates can read synchronized metadata through
`.Campaign.Attribs.post`:

```go-html-template
{{ $assetBaseURL := "https://blog.example.com" }}
{{ with .Campaign.Attribs.post }}
  <h1>{{ .title }}</h1>
  <p>{{ .description }}</p>
  <p>{{ .date }} · {{ .readingTime }}</p>
  {{ if .image }}<img src="{{ $assetBaseURL }}{{ .image }}" alt="" />{{ end }}
  <a href="{{ .url }}">Read on the web</a>
{{ end }}
```

Rendered images in the campaign body are already absolute. The base URL prefix
above supports a relative top-level `image` value.

[`blog-updates.gohtml`](blog-updates.gohtml) is a complete responsive example.
Its opening `TEMPLATE SETTINGS` block separates:

- site and publisher identity
- site, asset, logo, and fallback post URLs
- optional button visibility and email copy
- date formats and font stacks
- layout dimensions and colour palettes

At minimum, replace `baseURL`, `siteName`, `publisherName`, `postalAddress`,
and the logo settings. Set `assetBaseURL` separately when assets are served
from a CDN. `showReadPostButton` controls the optional button beneath the
campaign body.

Post links prefer the canonical `.Campaign.Attribs.post.url` value and fall
back to `baseURL`, `postPath`, and the campaign name only when that metadata is
absent.

## Container image

CI publishes AMD64 and ARM64 images to:

```text
ghcr.io/mitcdh/hugo-listmonk-sync
```

Run the latest image without building locally:

```sh
docker pull ghcr.io/mitcdh/hugo-listmonk-sync:latest
docker run --rm --env-file .env ghcr.io/mitcdh/hugo-listmonk-sync:latest
```

Successful pushes to the default branch publish `latest`, the branch name, and
a `sha-...` tag. A semantic version tag such as `v1.2.3` publishes `v1.2.3`,
`1.2.3`, `1.2`, and `1`. Pull requests and other branches are build-only.

The image contains no credentials or persistent application state, uses
`python:3.13-slim`, and runs as UID/GID 10001. SIGTERM and SIGINT interrupt the
polling wait for a clean shutdown.

## Development

The project requires Python 3.13 and uses
[`uv`](https://docs.astral.sh/uv/) with a checked-in lockfile:

```sh
uv sync --frozen --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

With the required environment variables already exported, run:

```sh
uv run hugo-listmonk-sync
uv run python -m hugo_listmonk_sync
```

Build and smoke-test the container against local mock services:

```sh
docker build -t hugo-listmonk-sync:smoke .
./scripts/container-smoke.sh hugo-listmonk-sync:smoke
```

The GitHub Actions workflow runs Ruff, formatting, strict mypy checks, and the
pytest suite before building the multi-platform image.

### Module layout

- [`config.py`](src/hugo_listmonk_sync/config.py) parses and validates
  environment-only configuration.
- [`feed.py`](src/hugo_listmonk_sync/feed.py) retrieves and validates schema v1
  feeds.
- [`listmonk.py`](src/hugo_listmonk_sync/listmonk.py) implements authenticated
  campaign API operations.
- [`reconcile.py`](src/hugo_listmonk_sync/reconcile.py) applies matching and
  mutation rules.
- [`loop.py`](src/hugo_listmonk_sync/loop.py) handles immediate startup,
  post-run waits, and shutdown.
- [`main.py`](src/hugo_listmonk_sync/main.py) wires the clients and console
  entry point together.
