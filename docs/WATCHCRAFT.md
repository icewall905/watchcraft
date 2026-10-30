# WatchCraft — "Watching the Internet for you."

WatchCraft evolves BetterTube into a self-hosted hub that goes on the internet for you. TV (`/`, `/tv`) remains
the default and is unchanged. Design, route map, provider registry and API contract: [HUB.md](HUB.md).

## What exists now (schema v2)

| Section | Route | What works |
|---|---|---|
| TV | `/`, `/tv`, `/tv?mode=tv` | Existing video home. `?mode=tv` switches the browser to TV mode for media-center launch. |
| Play on | Header → Play on | Persistent browser receivers, short-lived acknowledged commands, and phone play/pause/resume/stop; receiver credentials are hashed. Browser flow verified using generated local test media. |
| Discover | `/discover` | Unified feed of curator finds: unread / new since last visit / saved, type/provider/curator/score/date filters, sort, save, favorite, read later, dismiss, "Why?" provenance. |
| News | `/news` | News/article items by **publication** time (discovery time shown separately), grouped into stories by title overlap, follow/mute topics. |
| Knowledge | `/knowledge` | Long-lived topics: overview, user notes, linked items, recent matching finds (FTS), relevant curators, related topics. |
| Library | `/library` | Saved, favorites, read later, archive, collections (create/rename/pin/delete), URL import with page extraction, JSON export. |
| Item | `/item/[id]` | Detail with source facts vs AI enrichment kept apart, type-specific metadata, provenance, notes, tags, collections, topics, related items. |
| Briefings | `/briefings` | Manual/daily/weekly cited briefings over selected curators/topics; never repeats items from earlier issues; non-AI fallback digest. |
| Alerts | `/alerts` | Per-curator rules (min severity, keywords); new/read/resolved/snooze; advisories say "possibly relevant — verify affected versions". |
| Research (OSINT) | `/research` | One-off or ongoing public-source research on a topic, organization, person, domain or product; cited report; findings by type. |
| Curators | `/curators`, `/curators/new`, `/curators/[id]` | Natural-language wizard + templates, multi-source, edit, duplicate, pause/resume, run now, delete, run history, export JSON. |
| Sources | `/sources` | Provider catalogue with capabilities/availability, reusable sources, health, test connection. |
| Find | `/find`, Ctrl/Cmd+K | Local FTS5 search (prefix, "phrases", -exclusions) over items, private notes, local videos, curators, collections, topics; live web search on demand; saved + recent searches; "turn into a curator". |
| Settings → Search & sources | `/settings?tab=search` | SearXNG URL or Brave key (redacted), toggle Hub AI. |

Navigation is defined once in `frontend/src/lib/navigation.ts`. Desktop: grouped, collapsible sidebar with expanded/compact
modes and unread/alert counts. Phones (desktop mode, ≤760px): bottom bar TV · Discover · Library · Alerts · More.
TV mode: a deliberately small drawer (TV Home, Video search, Subscriptions, Playlists, Hub, Settings).

Providers: RSS/Atom (incl. YouTube channel feeds → video), GitHub releases, GitHub repository search, Hacker News,
arXiv, NVD CVE API, CISA KEV, Wikipedia, RDAP, web search (SearXNG/Brave), single-page extraction (OpenGraph +
JSON-LD Recipe/Article). Web search shows as unavailable until configured and is never silently enabled.

AI (optional, uses the existing OpenAI-compatible endpoint): relevance scoring with evidence-based reasons, curator
drafts, cited research reports and briefings. Without AI: keyword relevance, heuristic drafts, grouped digests.

## Architecture

- `backend/bt/hub/`: `models` (tables), `migrate` (versioned migrations), `providers` (registry/adapters),
  `extract` (page metadata), `engine` (runs, dedupe, routing to collections/topics/alerts, scheduler), `ai`, `api`, `templates`.
- Canonical `ContentItem` per normalized URL (tracking params removed). `CuratorDiscovery` keeps every curator↔item
  link with score, reason and query. `ItemState` is per profile. Source facts (`meta`) and AI output (`enrichment`) are separate.
- `content_fts` (SQLite FTS5) indexes title, summary, extracted text and people; private notes are searched per profile only.
- Scheduler runs in the API process every 30 s; a conditional `next_run` lease (15 min) prevents overlapping runs across
  processes and recovers after crashes. Each source fails independently; runs record `ok`/`partial`/`failed` with
  per-source messages that never include URLs or credentials.

## Security

- All retrieved-content fetches use `bt/curation.py`: HTTPS:443 only, every resolved address must be public, the socket is
  pinned to the validated IP, each redirect is revalidated, bounded size and content-type, XML entities rejected.
- The SearXNG URL is admin configuration (may be on the LAN); it is requested without following redirects.
- Retrieved HTML is reduced to text and allow-listed metadata; the UI renders text only. Only `https:` image URLs are kept.
- Prompts mark retrieved content as untrusted data; outputs are schema-checked; summaries carry `[n]` citations.
- Every `/api/hub` endpoint scopes by the active profile (existing PIN rules). Items are visible to a profile only if its
  curators found them or it saved/touched them. Brave API key is redacted in settings responses.
- Household access is implemented in `bt/access.py`, using the existing signed, server-verified profile PIN sessions.
  Profiles have `admin`, `adult`, or `child` access. Only administrators can change shared settings, credentials,
  proxy/AI connection tests, global worker controls, profile roles, or create/delete profiles. Adults can still edit
  their own preferences and manage their own Hub resources. Keep at least one administrator.
- An administrator without a PIN is intentionally an open household administrator: anyone can select that profile.
  Set a PIN to protect shared settings. The shared application password remains separate from household roles.
- Child profiles are conservatively blocked from **all** Hub APIs and pages, including registry/templates, local
  search, import, external search, source tests, custom prompts, reports and alerts. Child-safe Hub curation is not
  available yet. Child mode does not claim to filter arbitrary video playback; existing playlist kid-safe AI checks remain.
- Enabling a child profile requires PINs on **every** adult/administrator profile. Missing headers, guessed IDs,
  arbitrary preferences, profile creation and role edits cannot bypass the policy. Converting an existing profile to
  child pauses its curator and briefing schedules while retaining data. Workers also check the profile policy.
- Local PIN recovery generates a replacement adult PIN when child profiles exist; it never leaves a parent
  profile unprotected. Old PIN sessions are revoked. Without child profiles, the existing remove-PIN behavior remains.

## Migration and rollback

`init_db` runs `bt.hub.migrate` inside the existing `BEGIN IMMEDIATE`. v1 → v2 adds curator columns, creates reusable
sources from each v1 curator, and copies v1 discoveries into items, provenance and saved state. The v1 `discovery`
table is kept untouched. Video tables are not modified. Back up first (maintenance menu); roll back by restoring the backup
with the previous checkout. Tested by `tests/test_curation.py::test_v1_database_migrates_to_v2`.

The household-access migration adds `profile.role` before querying profiles. It preserves IDs, preferences and PINs,
makes the first existing profile the administrator, and leaves other existing profiles as adults. It is additive and
idempotent; `tests/test_access.py` tests an actual legacy database copy. Back up before upgrading. An old application
does not enforce the new roles, so rolling back must also restrict access until household protections are restored.

## Deploy on 10.0.10.73

1. Back up the existing installation with its maintenance menu before pointing this version at existing data.
2. Parallel install: `docker compose -p watchcraft -f compose.yaml -f compose.watchcraft.yaml up -d --build --wait`
   (Compose 2.24.4+). Never run two stacks against the same SQLite data directory.
3. `BT_PORT=6646` in `.env`. Optional: `WC_SEARCH_PROVIDER=searxng` and `WC_SEARXNG_URL=http://searxng:8080`
   (or configure in Settings → Search & sources). SearXNG must have the JSON format enabled.
4. Install `deploy/traefik-wc.yml` as `/opt/traefik/dynamic/wc.yml` on 10.0.10.60.
5. Media center: point the TV browser at `https://wc.tdgs.org/tv?mode=tv`.

## Source Scout

Under Sources, research a public HTTPS website or a topic (topic search requires the configured web-search provider).
Scout checks RSS, Atom and JSON Feeds advertised by the page or HTTP Link header, and tries a few common feed paths
when none are advertised. It validates each candidate using the same public-address, HTTPS, redirect and size checks
as the Hub. When no valid feed is available, it can test same-host links inside semantic HTML `article` elements as a
simple article-listing watcher. Sitemaps found in page metadata or robots.txt are displayed as unverified leads;
they are not currently monitored. Preview samples before explicitly adding a source, optionally to a curator. Adding
rechecks the source; the ordinary curator scheduler performs subsequent monitoring.

Runs and milestones are profile-owned and persisted in the existing activity store. Research may be repeated manually
or scheduled daily/weekly with pause/resume; scheduled runs only suggest sources. The scheduler claims due work atomically,
limits concurrent jobs, and disables plans when a profile becomes child-restricted. A run examines at most three search
results and five feed candidates per site; results can miss relevant sources. AI-generated watcher rules, automatic
source adoption, sitemap/page-change/browser watchers, repair, shared polling and automated relevance scoring remain
planned. This is a foundation, not the full autonomous research-team roadmap.

## Agent Activity and parallel agents

Automation → Agent Activity (`/team`) shows the real state of each agent: the video curator and downloader, every Hub
curator, Source Scout and the briefing writer, with what each is doing, recent finds and a journal of finished runs.
Nothing is simulated; idle agents show their last results. Settings → AI and limits → Agent team sets how many Hub
agents (scheduled curators, Scout searches, briefings) may work at once (`team.max_parallel`, 1–8, default 1).
Scheduled work waits for a free slot; anything a user starts begins at once but occupies a slot. The video curator
runs in its own worker and is not counted. AI calls remain serialized per process.

## Not implemented yet (honest list)

Semantic search/embeddings, child-approved Hub content (child profiles currently have no Hub access), advanced cron schedules and explicit run
cancellation, notification channels and quiet hours, additional phone→TV pairing/authorization hardening, custom sidebar ordering (only pinned
collections/topics), curator config import (export exists), Reddit/Mastodon/Stack Exchange/OSV/vendor advisories,
recipe/pattern site-specific adapters beyond JSON-LD, offline reading, per-task AI model selection.

## Prior submitted baseline

- Backend: full pytest suite green (hub tests in `tests/test_curation.py`: isolation, multi-curator provenance,
  FTS, partial failures, crash recovery, alerts, briefings, research fallback, page extraction, v1→v2 migration).
- Frontend: `npm run check` 0 errors; production build OK.
- Playwright (`npm run test:e2e`): all pass except 4 specs that also fail on the previous commit
  (`smoke` keyboard-only, `stats` viewport ×2, `status-pill` hover). New: `navigation.spec.ts`, `hub.spec.ts`.
- Not done: live-provider network tests, real SearXNG/Brave, TV remote pass on hardware, Docker build, deployment.

## October 30 P0 implementation

IMPLEMENTED: household roles and server authorization; conservative child Hub restrictions; PIN-safe local recovery;
invalid-profile rejection; News topic ownership checks; regression coverage across Hub owned resources and relation writes.
The TV hero now displays "Untitled video" for incomplete video metadata rather than rendering an empty heading.
Hub fallback browser tests explicitly disable AI so they remain independent of an external model server.

IMPLEMENTED: browser baseline repair. Statistics tables fit desktop viewports including 1024 px. Statistics fixtures
are idempotent when a worker restarts. The status-preview test explicitly selects a profile instead of being blocked
by the initial picker. The keyboard smoke test follows the current sidebar navigation. Spatial navigation no longer
restores a remembered control in the opposite direction, fixing a playlist-form D-pad focus loop.

VERIFIED: 252 backend tests passed, 6 skipped; 11 deployment/recovery tests passed; frontend type check (zero errors or
warnings) and production build passed. Focused browser runs passed for Hub, profile PINs, child/admin access, News/
Library/navigation rendering, desktop/sidebar/TV/mobile behavior, keyboard-only smoke, home focus, all five Statistics
viewports plus drilldowns, and the status preview. Screenshots of child blocking and Statistics were inspected.

P0 baseline audit COMPLETE: the reported navigation, routes, Hub models/providers, optional AI, scheduling and v2
migration are present in this checkout. Source facts, per-profile state and provenance reuse the existing implementation.
Provider adapters are present, but live external-provider availability has not been verified. Browser handoff is already
implemented; additional pairing/authorization hardening remains planned. At that point no Source Scout, programmable website watchers,
mixed home layout system, team activity, Scout Report or Meme Radar implementation was found; see the Source Scout section above for the subsequent foundation.

Additional verification: six browser playback/offline/error/back-navigation tests passed, including actual playback of
generated local media on a browser receiver. `tests/test_access.py` now has 24 passing cases, including a real SQLite
backup/restore followed by application initialization and checks of roles, PIN records, discoveries and the FTS index.
The complete backend run before adding that final restore case passed 252 tests with 6 skipped.

DEPLOYED: on 2026-10-30, revision `43f6230` was migrated and deployed at `10.0.10.73:6646`, followed by
frontend navigation fix `880f732`. The previous image, source archive and pre-migration backup are retained under
`/root/compose/watchcraft-backups/20261030T090143Z-43f6230`; that backup excludes the separate media volume. A
restored copy was migrated twice and checked for integrity, matching profile preferences and preserved profiles,
playlists, video records, history and legacy discoveries. The existing NVIDIA Compose override was retained.

VERIFIED ON SERVER: API health and the public `https://wc.tdgs.org` health endpoint; eleven Hub/TV routes; adult
authorization and cross-profile isolation; and a saved video playing at 1920×1080 with decoded, changing frames.
A disposable container on the same server passed the child-profile, administrator PIN and player-error browser
checks without changing production PINs or child profiles. The live acceptance test removed its temporary profile.
The navigation fix also passed local player-error and playlist Back tests. Physical TV remote acceptance and live
external-provider acceptance are still outstanding.

PLANNED: advanced Source Scout and declarative watchers, shared source polling, persistent team activity, Scout Report, home layout
presets, Meme Radar and the remaining advanced capabilities in `todo.md`. Existing item pages, collections, News
grouping, cited reports and search filters already provide parts of the P4 experience; they must be extended in place.

Production acceptance used a temporary profile, which was removed, and a disposable server-side database for child-profile checks. The remaining roadmap continues with advanced Source Scout and the shared research-team experience.
