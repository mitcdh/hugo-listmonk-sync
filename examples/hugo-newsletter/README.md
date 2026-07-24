# Hugo newsletter feed example

This directory is a self-contained reference implementation of the Hugo side
of the newsletter workflow. It documents and tests the complete
feed-generation contract without depending on a separate site repository.

The files under `layouts` implement the newsletter JSON output and its
email-safe URL handling. `hugo.toml` is a minimal reusable configuration with
an example `baseURL` and home output list. `content/posts/example.md` is a
small fixture that exercises post selection, relative URL conversion,
`srcset` conversion, and protection of Go template delimiters in displayed
code.

## Layout

```text
examples/hugo-newsletter/
├── content/posts/example.md
├── hugo.toml
└── layouts/
    ├── _default/list.html
    ├── _default/single.html
    ├── home.html
    ├── home.newsletter.json
    └── partials/newsletter/
        ├── absolute-srcset.html
        ├── absolute-url.html
        ├── absolute-urls.html
        └── escape-code-delimiters.html
```

## Build

From the repository root, run:

```sh
hugo --source examples/hugo-newsletter
jq . examples/hugo-newsletter/public/newsletter.json
```

The generated feed contains only pages in the `posts` section whose front
matter sets `newsletter: true`. Its rendered `html` field uses absolute asset
and link URLs, while the top-level `image` field retains the front matter value
for campaign-template metadata.

The small `home.html` and `_default` HTML files only make this example
independently buildable without a theme. Copy the newsletter layout and
partials into an existing Hugo site's own layout system.

When the feed contract changes, update the layouts and this example together.
Increment `schemaVersion` in `layouts/home.newsletter.json` for a breaking
contract change, then update the synchronizer validation and tests to support
that version deliberately.
