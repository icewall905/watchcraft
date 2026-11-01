# Security review

Scope: the WatchCraft server as deployed (`wc-api`, `wc-worker`, `wc-downloader` behind a
reverse proxy). This is a household appliance on a private network, optionally exposed at
`https://wc.tdgs.org`. It is not multi-tenant and does not defend against a hostile
administrator — an administrator can already read the database.

Each section states what is enforced, where, and what it does **not** cover. Tests are named
so a claim can be checked rather than trusted.

## Trust boundaries

| Boundary | Crossing | Enforced by |
|---|---|---|
| Internet → server | every fetched URL | `bt/curation.py` |
| Browser → API | session cookie, `X-Profile-Id` | `bt/auth.py`, `bt/access.py` |
| Profile → profile | every row read or written | `owned()` / `profile_id` dependency |
| Child profile → Hub | discovery, research, memes | `require_hub` |
| Retrieved content → prompt | curator and research prompts | `bt/hub/ai.py` |
| Server → LAN services | SearXNG, webhooks | admin configuration only |

## Authentication and authorization

- **Shared passphrase** (`BT_PASSWORD`) is optional. When set, `AuthMiddleware` rejects every
  `/api/*` path except `/api/health`, `/api/auth/login` and `/api/auth/status` with 401. The
  session is a signed cookie (`itsdangerous`, `SECRET`), `httponly`, `samesite=lax`, one year.
  When unset, the API is open — the deployment relies on the network boundary. This is a
  deliberate appliance trade-off, not an oversight.
- **Profiles** carry a role: `admin`, `adult` or `child`. `require_admin` gates shared settings,
  credentials, proxy/AI tests, worker controls, profile roles and profile creation/deletion.
  The last administrator cannot be deleted or demoted (`validate_household`).
- **Profile PINs** are optional per profile, PBKDF2-SHA256 with 260k iterations and a random
  salt, stored as a hash. Five wrong attempts lock the profile for 30 seconds. A signed cookie
  records the unlock and is invalidated when the PIN changes (`version`).
- **Child profiles** require a PIN on every adult and administrator profile before they can be
  created. Children are blocked from the whole Hub (`require_hub`) and from Hub routes in the
  navigation. They can still watch video and cast to a paired screen, which is the intended
  family behaviour.
- **Cross-profile isolation** is enforced per endpoint: `owned()` raises 404 (not 403) for a
  foreign row so an id cannot be used as an existence oracle. Filter ids supplied by the client
  (curator, source, topic) are ownership-checked before they reach a query.
  Covered by `tests/test_access.py`, `tests/test_curation.py::test_meme_api_is_profile_scoped`.

## Outbound fetching (SSRF)

`bt/curation.py:fetch` is the only path for untrusted URLs:

- HTTPS on port 443 only; the scheme and port are not negotiable.
- Every resolved address must be globally routable (`ipaddress.is_global`). A hostname that
  resolves to any private, loopback, link-local or reserved address is refused.
- The validated IP is pinned to the socket, so a second DNS answer cannot redirect the
  connection (DNS rebinding).
- Each redirect is revalidated through the same checks, up to four hops.
- Response size is bounded (2 MB default) and the content type must be on an allowlist.
- XML with a `DOCTYPE` or `ENTITY` declaration is rejected before parsing (XXE).
- Credentials in headers are only sent to the original host, never across a redirect.

**Deliberate exception:** administrator-configured service URLs — the SearXNG instance and the
notification webhooks — are requested directly with `httpx`, no redirect following, bounded
response. They are expected to live on the LAN, so the public-address rule cannot apply. Only
an administrator can set them, and they are validated to start with `http://` or `https://`.

Covered by `tests/test_curation.py::test_private_network_and_entity_rejection`.

## Secrets

- The Brave API key, the LLM API key, the proxy password and the notification email password
  are replaced with `********` in every settings response (`_redact`). Saving the redacted
  value keeps the stored secret, so a form round-trip cannot erase it.
- Playback receiver tokens are random (`secrets.token_urlsafe(32)`); only their SHA-256 hash is
  stored. Public discovery never returns them, and the pairing code is only ever returned to
  the registered receiver's own heartbeat.
- The YouTube cookies file is written with mode `0600`.
- `.env` is never copied into the repository and is excluded from the rsync rollout.

## Abuse limits

| Limit | Value | Where |
|---|---|---|
| Playback commands per screen | 30 per 10 s | `api/playback.py` |
| Wrong pairing codes | 5 per 10 min, then 429 | `api/playback.py` |
| Registered screens | 10 | `api/playback.py` |
| Profiles paired per screen | 20 | `api/playback.py` |
| Wrong profile PINs | 5, then 30 s lockout | `profile_pins.py` |
| Curators per profile | capped | `hub/api.py` |
| Concurrent agents | 1–8, configurable | `hub/slots.py` |

## Prompt injection

Retrieved text is untrusted. It is passed inside `<data>` blocks with an explicit instruction
to ignore instructions found inside it, and any `<`, `>` or `&` in the content is escaped so it
cannot close the block. Model output is schema-validated before use, and summaries carry `[n]`
citations back to stored items. A model that ignores the guard can still produce a wrong
summary; it cannot execute anything, because nothing in the pipeline evaluates model output.

## Capture rescue and the browser sandbox

When a page defeats the plain fetch — it renders its article in JavaScript, hides it behind a
cookie or bot wall, or ships a scan — `hub/rescue.py` tries other ways to get the same text.
Most tools are ordinary bounded fetches through `curation.fetch`, so they inherit the SSRF rules
above. Two are different:

- **`render`** is disabled on **every host**, even when bubblewrap can start. Filesystem
  isolation does not enforce the public-network fetch rules: Chromium redirects and subresources
  can contact localhost or LAN services. `tool_render` returns without launching a process.
  Container capabilities cannot enable it. Ordinary page recovery still uses validated fetches.
- **`ocr`** runs `pdftoppm` and `tesseract` on a PDF already fetched through `curation.fetch`,
  in a temporary directory, reading at most ten pages. An eleventh rendered page detects truncation;
  page and text limits are retained in the reader's partial-text notice.
- **NVD records** recover from the official NVD JSON API when the human page returns a JavaScript
  challenge. The route requires the exact NVD hostname and a valid CVE path, verifies the returned
  record ID, and keeps the API source URL with the extracted description.

The model chooses which tool to try next, but it can only name a tool that exists. It never
supplies a command, a URL to fetch, or a shell string, so a hostile page cannot turn the choice
into execution. Adding a script runner later would break that property and needs its own
sandbox review.

## Known limits

- **No CSRF token.** The session cookie is `samesite=lax`, which blocks cross-site form posts
  but not every cross-site request. A hostile page on the same site could act as the user.
  Acceptable for a single-household appliance; not acceptable if this ever becomes multi-tenant.
- **No rate limit on login.** `POST /api/auth/login` compares with `hmac.compare_digest` but
  does not throttle. The passphrase is the only defence.
- **The API is open when `BT_PASSWORD` is unset.** Anyone who can reach the port has full
  access, including administrator actions.
- **Notification webhooks can reach the LAN.** That is the point (Home Assistant), but it means
  an administrator can make the server POST to any internal address.
- **Browser rendering is disabled on every host.** JavaScript-only pages without an API, feed
  or other readable version still fail capture (see above).
- **No audit log of administrator actions** beyond the agent event stream.
- **Media files are served by id**, not by signed URL. Anyone with the session can fetch any
  stored video; there is no per-profile media authorization.
