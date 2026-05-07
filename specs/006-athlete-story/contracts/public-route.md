# Public Route Contract

Lives in a NEW module `running_coach_ai/web/routes/public_story.py`. Registered as a Flask blueprint without `url_prefix` so its routes resolve at the application root.

---

## `GET /story/<share_token>`

Public, server-rendered Jinja2 page presenting the story in its active magazine template. **No login required.**

### Path parameters

- `share_token` — 12-character URL-safe string. Validates against `[A-Za-z0-9_-]{12}`; anything else returns 404 immediately (no DB lookup).

### Query parameters

- `preview=1` (optional) — when present, requires login as the owning athlete OR as an admin; allows viewing an unpublished story.

### Response 200 (HTML)

Renders `web/templates/story_page.html` which includes the chosen template's `cover.html` + `inside.html` from `web/static/story_templates/<key>/`. Page includes:

- `<meta name="robots" content="noindex,nofollow">` (FR-S025)
- `<link rel="canonical" href="<share_url>">` (so social-card crawlers don't see ?preview=1)
- The magazine's existing Google Fonts (DM Serif Display, Bebas Neue, Inter) — no additional font loading per the existing assumption
- The chosen template's `template.css`
- Photo grid populated from `StoryImage` rows ordered by `sort_order`

### Response 404

Returned (with no information leakage about WHY) when:
- `share_token` is malformed
- No `AthleteStory` row matches
- `deleted_at IS NOT NULL`
- `published_at IS NULL` and the request lacks `?preview=1` OR the requester is not the owning athlete / not admin

### Response 429

Per FR-S026 — returned when the per-IP sliding window (60 s, 30 requests) is exceeded. Body: plain text `"Too many requests"`.

### Headers

- `Cache-Control: private, max-age=60` for published successful renders (light caching to absorb retries on social shares without complicating cache invalidation).
- `Cache-Control: no-store` for `?preview=1` responses (drafts can change at any moment).

### IP resolution

Behind a reverse proxy (Railway), the route trusts the leftmost entry in `X-Forwarded-For`, falling back to `request.remote_addr`. Explicit:

```python
ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
```

---

## `GET /robots.txt`

Per FR-S025 — minimal robots policy disallowing the public story namespace.

### Response 200 (text/plain)

```
User-agent: *
Disallow: /story/
```

### Cache headers

`Cache-Control: public, max-age=86400` — robots.txt rarely changes.

---

## Rate-limit data structure

Implementation detail (research R-005) — declared here so contract tests can assert behavior:

- One Python `dict[str, deque[float]]` mapping IP → recent request timestamps
- Single `threading.Lock` guarding the dict
- Each request: prune timestamps older than `time.monotonic() - 60`; if remaining count ≥ 30, return 429; else append now and continue
- Resets on Flask app restart (acceptable per research notes)
- Memory ceiling: ~30 KB at 100 unique IPs × 30 timestamps; no eviction policy needed for v1
