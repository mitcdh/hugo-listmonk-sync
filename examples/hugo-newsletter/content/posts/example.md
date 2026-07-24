---
title: "Newsletter feed example"
date: 2025-01-01T12:00:00+10:00
description: "A fixture that exercises the Hugo newsletter feed."
image: "/images/newsletter-example.webp"
newsletter: true
tags: [Writing, Technology]
---

This post links to the [about page](/about/) and includes a root-relative
image:

<img src="/images/newsletter-example.webp" srcset="/images/newsletter-example-640.webp 640w, /images/newsletter-example-1280.webp 1280w" alt="A newsletter feed example.">

Displayed Go template actions must reach Listmonk as code instead of being
evaluated as part of the campaign:

```go-html-template
{{ with .Campaign.Attribs.post }}
  <h1>{{ .title }}</h1>
{{ end }}
```
