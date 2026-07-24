# Hugo to Listmonk campaign synchronizer

A small, standalone Python 3.13 service that polls a Hugo newsletter JSON feed
and reconciles its posts with Listmonk campaigns. It creates missing campaigns
as drafts, refreshes matching drafts, and treats every non-draft campaign as
immutable.

The service targets Listmonk 6.0 or later. Campaign-level `attribs` were added
in Listmonk 6.0 and are used to expose Hugo post metadata to campaign
templates.

## Safety model

Each cycle is deliberately conservative:

1. Fetch and validate the entire `schemaVersion: 1` feed, including duplicate
   campaign keys, before making any Listmonk mutation.
2. Retrieve all accessible campaigns with
   `GET /api/campaigns?per_page=all&no_body=true`.
3. Match the configured post field to campaign names exactly and
   case-sensitively.
4. Create a draft if there is no match.
5. Fetch and update a single matching draft.
6. Skip a single matching non-draft campaign.
7. Treat two or more same-name campaigns as ambiguous and mutate none of them.

The service never calls Listmonk status, send, archive, or delete endpoints. It
never recreates a sent, scheduled, running, paused, cancelled, finished, or
otherwise non-draft campaign. Campaigns absent from the feed are left alone.

Draft updates replace only the campaign name, subject, body, and
`attribs.post`. Existing lists, sender, template, tags, messenger,
campaign/content types, alternate body, headers, `send_at`, and all other
top-level keys inside `attribs` are retained from the full campaign response.
Creation-only defaults do not overwrite settings on existing drafts.

## Feed contract

The feed root must be a JSON object with exactly `schemaVersion: 1` and a
`posts` array:

```json
{
  "schemaVersion": 1,
  "posts": [
    {
      "key": "parsing-consensus",
      "url": "https://blog.mitcdh.au/posts/parsing-consensus/",
      "tags": ["Writing", "Nuclear"],
      "date": "2026-07-18T00:00:00+10:00",
      "description": "Experiments in parsing IAEA guidance",
      "image": "/images/parsing-consensus.webp",
      "readingTime": 12,
      "title": "Parsing Consensus",
      "html": "<p>Newsletter body</p>",
      "text": "Newsletter body"
    }
  ]
}
```

Name, subject, and content selectors are literal top-level keys, not JSONPath
expressions. Their values must be non-empty strings. Every post name must be
unique for the cycle.

`attribs.post` contains a deep copy of every top-level post field except the
large `html` and `text` bodies. Metadata values and relative image paths remain
unchanged. A numeric `readingTime` is normalized to `"<N> min read"`; an
existing string is preserved.

## Listmonk requirements

Use Listmonk 6.0 or newer and create a dedicated API user under **Admin →
Users**. Authenticate with that API user's username and token using HTTP Basic
authentication.

Give the API user campaign read and management permissions
(`campaigns:get`/`campaigns:manage`, or the corresponding `*_all`
permissions), plus an attached list role that permits access to every ID in
`LISTMONK_LIST_IDS` and to lists already attached to drafts it may update.
Listmonk 6.1 separates `campaigns:send`; this service does not require or use
that permission.

`LISTMONK_BASE_URL` is the origin, such as `https://listmonk.example.com`, and
must not include `/api`.

## Configuration

Configuration is exclusively through environment variables. Copy
`.env.example` to `.env` for Docker Compose; never commit the resulting file.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `NEWSLETTER_JSON_URL` | yes | — | Absolute HTTP(S) URL of the Hugo JSON feed. |
| `LISTMONK_BASE_URL` | yes | — | Listmonk origin without a path or `/api`. |
| `LISTMONK_API_USERNAME` | yes | — | Dedicated Listmonk API username. |
| `LISTMONK_API_TOKEN` | yes | — | API user's secret token. |
| `LISTMONK_LIST_IDS` | yes | — | Comma-separated, unique positive list IDs for new campaigns. |
| `CAMPAIGN_NAME_FIELD` | no | `key` | Exact top-level post key used as campaign name and sync key. |
| `CAMPAIGN_SUBJECT_FIELD` | no | `title` | Exact top-level post key used as subject. |
| `CAMPAIGN_CONTENT_FIELD` | no | `html` | Exact top-level post key used as body. |
| `LISTMONK_CONTENT_TYPE` | no | `html` | New campaign content type: `richtext`, `html`, `markdown`, `plain`, or `visual`. |
| `LISTMONK_CAMPAIGN_TYPE` | no | `regular` | New campaign type: `regular` or `optin`. |
| `LISTMONK_MESSENGER` | no | `email` | New campaign messenger, including a configured custom messenger name. |
| `LISTMONK_TEMPLATE_ID` | no | unset | Positive template ID for new campaigns; omitted when unset. |
| `LISTMONK_FROM_EMAIL` | no | unset | Sender for new campaigns; omitted when unset so Listmonk uses its default. |
| `LISTMONK_CAMPAIGN_TAGS` | no | unset | Comma-separated tags applied only when creating campaigns. |
| `POLL_INTERVAL_SECONDS` | no | `3600` | Positive integer wait after each completed cycle. |
| `HTTP_TIMEOUT_SECONDS` | no | `30` | Positive request timeout in seconds; decimals are accepted. |
| `HTTP_MAX_RETRIES` | no | `3` | Non-negative retry count after the initial safe request. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `RUN_ONCE` | no | `false` | `true` runs one immediate cycle and exits. |

The first synchronization begins immediately at startup. In continuous mode,
the interval starts only after that cycle completes, so slow runs never overlap.
`RUN_ONCE=true` is useful with an external scheduler and in deployment tests.

Feed GETs and Listmonk GET/PUTs retry HTTP 429, transient 5xx responses, and
network failures with bounded exponential backoff and `Retry-After` support.
Campaign POSTs are attempted exactly once because Listmonk does not document an
idempotency key. If a POST result is uncertain, the next cycle discovers a
successfully created campaign by its name.

Normal TLS certificate verification is always enabled. For private certificate
authorities, mount the CA bundle into the container and set the standard
`SSL_CERT_FILE` (or `SSL_CERT_DIR`) environment variable to its in-container
path. There is intentionally no insecure TLS switch.

## Run with Docker

Build and start a one-off container:

```sh
cp .env.example .env
# Edit .env and replace all example credentials and endpoints.
docker build -t hugo-listmonk-sync:local .
docker run --rm --env-file .env hugo-listmonk-sync:local
```

Or use the example Compose service:

```sh
docker compose up --build -d
docker compose logs -f hugo-listmonk-sync
docker compose down
```

The image contains no credentials or persistent state, runs as UID/GID 10001,
and uses `python:3.13-slim`. SIGTERM and SIGINT interrupt the interval wait for
clean container shutdown.

### GitHub Container Registry

CI publishes a multi-platform image for AMD64 and ARM64 to:

```text
ghcr.io/mitcdh/hugo-listmonk-sync
```

Every successful push to the repository's default branch publishes `latest`,
the branch name, and a commit-based `sha-...` tag. Pushing a semantic version
tag such as `v1.2.3` also publishes `v1.2.3`, `1.2.3`, `1.2`, and `1`.

Pull and run the latest image with:

```sh
docker pull ghcr.io/mitcdh/hugo-listmonk-sync:latest
docker run --rm --env-file .env ghcr.io/mitcdh/hugo-listmonk-sync:latest
```

Pull requests and non-default branches build the image but never publish it.
Publication uses the workflow's built-in `GITHUB_TOKEN`; no registry secret is
required.

GitHub makes a newly published container package private by default. After the
first successful default-branch build, open the package settings on GitHub and
change its visibility to **Public** if anonymous pulls are desired. Private
packages require an authenticated `docker login ghcr.io`.

## Template metadata

Listmonk templates can read the synchronized map through
`.Campaign.Attribs.post`:

```go-html-template
{{ with .Campaign.Attribs.post }}
  <h1>{{ .title }}</h1>
  <p>{{ .description }}</p>
  <p>{{ .date }} · {{ .readingTime }}</p>
  {{ if .image }}<img src="{{ .image }}" alt="" />{{ end }}
  <a href="{{ .url }}">Read on the web</a>
{{ end }}
```

The repository's `blog-updates.gohtml` is a fuller Listmonk campaign-template
example.

## Local development

Dependencies are resolved in `uv.lock`.

```sh
uv sync --frozen --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

Run the installed console command or module with environment variables already
exported:

```sh
uv run hugo-listmonk-sync
uv run python -m hugo_listmonk_sync
```

Build and smoke-test the container against local mock feed/Listmonk endpoints:

```sh
docker build -t hugo-listmonk-sync:smoke .
./scripts/container-smoke.sh hugo-listmonk-sync:smoke
```

The CI workflow runs Ruff lint/format checks, strict mypy checking, and the
pytest suite before building the Docker image. Successful default-branch and
`v*` tag builds are published to GHCR; pull requests and other branches are
build-only.

## Module layout

- `config.py` parses and validates environment-only configuration.
- `feed.py` fetches and fully validates schema v1 feeds.
- `listmonk.py` implements authenticated campaign API operations and payloads.
- `reconcile.py` applies exact matching and mutation rules with cycle summaries.
- `loop.py` implements immediate startup, fixed post-run waits, and shutdown.
- `main.py` wires the clients and console entry point together.
