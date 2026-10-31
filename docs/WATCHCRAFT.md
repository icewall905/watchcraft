# WatchCraft — "Watching the Internet for you."

WatchCraft evolves BetterTube into a self-hosted hub that goes on the internet for you. TV (`/`, `/tv`) remains
the default and is unchanged. Design, route map, provider registry and API contract: [HUB.md](HUB.md).

## What exists now (schema v2)

| Section | Route | What works |
|---|---|---|
| TV | `/`, `/tv`, `/tv?mode=tv` | Existing video home. `?mode=tv` switches the browser to TV mode for media-center launch. |
| Play on | Header → Play on | Persistent browser receivers with registration capped at 10 screens; senders pair once per screen with the screen's 6-digit pairing code (shown on the screen and re-derivable from a receiver heartbeat), per-screen send rate limit of 30 commands per 10 s. Any profile, child included, pairs the same way. Senders see the screen's progress and can seek (slider, ±10 s). Commands stay short-lived and acknowledged; receiver credentials remain hashed. Verified in `tests/playback.spec.ts` (pairing UI + real generated media), including the wrong-code path in `tests/test_playback.py`. |
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
simple article-listing watcher. It also tests up to five advertised or robots.txt XML URL sitemaps. Validated sitemaps
can be added as sources. Sitemap sources follow nested same-host indexes within fixed bounds (see Sitemap change tracking).
Preview samples before explicitly adding a source, optionally to a curator. Adding
rechecks the source; the ordinary curator scheduler performs subsequent monitoring.

Runs and milestones are profile-owned and persisted in the existing activity store. Research may be repeated manually
or scheduled daily/weekly with pause/resume; scheduled runs only suggest sources. The scheduler claims due work atomically,
limits concurrent jobs, and disables plans when a profile becomes child-restricted. A run examines at most three search
results and five feed candidates per site; results can miss relevant sources. AI-generated watcher rules, automatic
source adoption (OFF/SUGGEST/AUTO policies), browser watchers and automated relevance scoring remain planned.

### Watcher rules

Article listing, page change and sitemap sources have editable, declarative rules under **Sources → Watcher rules**.
Rules are data only: CSS selectors and URL path patterns such as `/blog/*`. No source runs code supplied by a person
or an agent. Selectors use tags, classes, IDs and attributes with at most three steps, at most one of them an
any-depth (space) step, the rest direct children (`main .post > h2`), and four comma alternatives. Pseudo-classes such
as `:not` or `:nth-child` and the `~` combinator are refused. With selector rules, pages deeper than 64 levels or larger
than 50,000 elements are refused and at most 1,000 item containers are read. Selector rules also run in a separate
process that is stopped after 20 seconds, so a page built to make matching slow fails that check instead of tying up
the server. Default rules use no selectors, run in the server process and work on any page.

- **Article listing:** item container, link, title and date selectors, allowed/ignored path patterns, ignored page
  areas, and dropping query strings. Default rules are unchanged: every same-host link with a title inside an
  `<article>`. With an item container, each item gives one link: a headline's own link wins over a leading image or
  "Read more" link. Generic link texts ("Read more", "Continue reading") are never used as titles.
- **Page change:** a content-area selector, ignored areas, and an optional alert when the page changes by at least a
  chosen number of characters.
- **Sitemap:** allowed/ignored path patterns and dropping query strings.

**Test rules** runs them against the live page and shows extracted titles, URLs, dates and excerpts without saving.
**Save** tests again and refuses rules whose test fails unless you choose **Save anyway** (for example while a site is
down); a saved rule set is never reported as working without that live result. Every change is kept as a revision
(the rules in service before the first change are kept as "original"), and **Restore** re-applies one with a fresh
test; a restore whose test fails also needs **Restore anyway**. Different rules on the same URL never share cached
responses. Changing a source's URL clears its rules and their history. After a page watcher's rules change, the first
observation under the new rules is recorded in history but never alerted, and a run that started under the old rules
records nothing.

### Sitemap change tracking

Sitemap sources follow nested same-host sitemap indexes breadth-first (at most 50 documents, three index levels and
5,000 URLs), reading each index's newest sitemaps first by lastmod, and read gzip-compressed sitemaps up to 20 MB
expanded. The root may redirect (for example to `www.`); children must stay on that host. An unreadable child is
skipped; the check fails only if nothing usable remains. Source Scout validates a sitemap by reading at most four
documents. Every listed URL is remembered per source, and each curator keeps its own position: its first read of a
sitemap uses the 50 most recently modified URLs, later reads every URL that appeared or got a newer lastmod since its
previous read, whichever curator's run noticed it. **View changes** lists new and updated URLs. URLs no
longer listed for 30 days are forgotten. Entries still use the URL as their title and article text is not fetched;
an updated URL that a curator already found is not reported as a new discovery.

### Source health and repair

Each source shows a health state from its last 20 checks: **healthy**, **degraded**, **broken**, **paused** or
**unknown**, with success rate, typical item count, trend and plain-language signals (rate limiting, refused access,
outage, rules extracting nothing, format change, or far fewer items than usual, which suggests a page redesign).
Three failed checks in a row, two in a row that extracted nothing, or three successful checks with no items mark a
source broken.

**Repair** revalidates the current rules, asks Source Scout's inspector for other validated ways to follow the site
(feeds first), re-reads the page structure, and proposes up to three rule sets. Each proposal has passed its own
live extraction and is compared with the typical count and the current sample. Proposing changes nothing; **Apply**
saves a proposal as a revision. A page watcher whose content area disappeared is offered the whole page instead.
With **Repair automatically** enabled, a broken listing or page watcher applies the best proposal on its own, at most
once a day: a listing only if it extracts at least three items and at least half the usual number, a page watcher only
if the whole page extracts again. The previous rules remain available for restore, and a failed attempt never fails
the curator run. Alternatives such as a newly found feed are only suggested, never added automatically.

### Page change watcher

Add a **Page change watcher** under Sources and attach it to a curator. Scout can also suggest one when a site has
readable text but no usable feed, listing or sitemap; adding it rechecks the page. Successful curator runs fetch the public
HTTPS page and retain the first observation as a baseline, then save a revision when its extracted title or readable
text changes. Open **View history** on the source to compare up to 20 retained observations; each shows the paragraphs added and
removed since the previous observation, and the full observed text remains available. Connection tests do not
create a baseline. Revisions belong to the source's profile and are removed when the source or profile is deleted,
or when the source URL changes. Pausing the source or detaching it from curators stops checks.

This is text comparison, not a semantic change detector: dynamic page content may cause noise (narrow it with a
content-area selector and ignored areas), navigation and scripts are excluded, only the first 20,000 extracted
characters are compared, and JavaScript rendering is not supported. Changes appear in source history and, when the
watcher's change alert is on, as one alert per page in Alerts that is raised again on each later change. They are
not new Discover items. The revision table is additive and is created at
startup, so follow the backup and restore procedure before deploying.

### Source check history

Each source can show its latest 20 recorded checks, including curator runs, connection tests and successful Scout
validation. A multi-query run that returns results but also fails a query is marked partial, with the error retained.
Older checks from before this feature are not reconstructed. History belongs to the source's profile and is removed
with the source or profile, or when its URL changes. Source health and repair (above) are computed from it.

### Shared source polling

Scheduled curators reuse a successful feed response for up to five minutes when the provider and exact endpoint
match. Only raw public feed results are shared; each profile keeps its own sources, scoring, discoveries, page history
and access rules. Query providers run independently. Manual curator runs, connection tests and Scout acceptance
request a fresh response; concurrent requests can reuse a fetch completed after their request began.

The latest 256 completed endpoint responses are stored in an additive `sourcepoll` table, with a two-megabyte limit
per response. Failed fetches are reused for at most 30 seconds to avoid retry storms. A database lease prevents
duplicate concurrent polls, is renewed during slow fetches, and expires after three minutes if a process dies. Waiting
checks stop after 190 seconds. History labels reused results and keeps the original fetch timestamp; reuse does not
pretend a new network check happened. Successful results survive API restarts until their five-minute freshness expires.

## Agent Activity and parallel agents

Automation → Agent Activity (`/team`) shows the real state of each agent: the video curator and downloader, every Hub
curator, Source Scout and the briefing writer, with what each is doing, recent finds and a journal of finished runs.
Nothing is simulated; idle agents show their last results. Settings → AI and limits → Agent team sets how many Hub
agents (scheduled curators, Scout searches, briefings) may work at once (`team.max_parallel`, 1–8, default 1).
Scheduled work waits for a free slot; anything a user starts begins at once but occupies a slot. The video curator
runs in its own worker and is not counted. AI calls remain serialized per process.

Curator cards, Source Scout and the Briefing Writer expose an expandable run timeline. Curator milestones record real source checks,
reused fetches, matching, optional AI ranking, completion and report-writing steps as those operations occur. Source
errors have expandable technical details. Timeline steps that name a source also carry live provider metrics
(`bt/hub/api.py _source_metrics`): provider/mode, last check status/count/time, shared-cache reuse, the source's
health summary (success rate, typical count, consecutive failures, trend, bounded signals) from the real check
history, and the latest run reports its duration. The existing event store retains up to 80 milestones per curator
and shows the latest 12 from its latest run. Events belong to the profile and are excluded from the shared video-agent
event/SSE feeds; deleting the curator/profile removes them. New runs gain milestones; earlier runs keep their existing
summaries. Briefing milestones record discovery selection, AI writing or the source-linked fallback, saving and
completion. The writer keeps up to 80 events per briefing, shows the latest run of the active briefing (or most
recently recorded run while idle), and removes those events when the briefing is deleted. Completion follows the issue
commit. Briefing generation rechecks profile access before saving and performs AI writing outside a database session. Narration follows
the per-agent voices described below.
Recent curator discoveries expand into source previews: a bounded source summary, publisher/source label, published
and discovery timestamps when known, the curator's separate note, an internal finding link and the original URL.
Summaries come from stored source metadata; missing summaries are labeled instead of generated. Preview retrieval is
profile-scoped and keeps the original discovery/source relationship.

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

PLANNED: advanced Source Scout and declarative watchers, persistent team activity, Scout Report, home layout
presets, Meme Radar and the remaining advanced capabilities in `todo.md`. Existing item pages, collections, News
grouping, cited reports and search filters already provide parts of the P4 experience; they must be extended in place.

Production acceptance used a temporary profile, which was removed, and a disposable server-side database for child-profile checks. The remaining roadmap continues with advanced Source Scout and the shared research-team experience.

SOURCE MONITORING DEPLOYED — 2026-10-30: release `ed95110` is running on the production API, worker and downloader.
Backup and practical rollback details are recorded in `docs/SOURCE_SCOUT_HANDOVER.md`. The restored-copy migration,
real page/feed/sitemap acceptance, public health, monitoring APIs, deployed assets and cached-video delivery/decode
passed. Original profile data, PIN records and checked content counts were preserved. Remaining roadmap items include
shared source polling, configurable extraction, automatic repair, notifications and the broader Scout Report experience.

SHARED POLLING DEPLOYED — 2026-10-30: release `96ec4de` is live across the application services. The complete backend
suite passed 282 tests (six live tests skipped), and ten browser checks passed. A production backup was restored and
initialized twice; real source-fetch acceptance confirmed one fetch reused by a second request. Data preservation,
production health, schema, monitoring APIs/assets and cached-video delivery/decode passed. Backup and rollback are in
`docs/SOURCE_SCOUT_HANDOVER.md`. P2's existing cards, narration, run journals and Scout progress were audited; unified
curator step timelines and the optional home experience remain, followed by Scout Report and the larger roadmap.

CURATOR TIMELINES DEPLOYED — 2026-10-30: release `e37d0e2` adds real private curator milestones and fixes the
shared-poll freshness race under lock contention. Full backend suite: 284 passed, six skipped; ten release browser
checks passed. Restored-copy migration/real-source tests and production data, API/assets and saved-video acceptance
passed. See `docs/SOURCE_SCOUT_HANDOVER.md` for the verified backup and the privacy-preserving rollback helper required
when reverting to an older image that does not filter the new private event kind.

PREVIEWS AND BRIEFING PROGRESS DEPLOYED — 2026-10-30: release `3f91e6d` is live. Recent findings expose source
summaries/metadata, original links and separate curator notes. Briefing Writer shows real selection, writing/fallback,
saving and completion milestones. Backend 286 passed/six skipped; frontend checks/build and ten browser checks passed.
Restored-copy and real-source briefing-fallback acceptance, production health/assets/APIs and saved-video delivery/decode
passed with original profile/PIN data and checked counts preserved. Backup and rollback are in the handover document.

### Optional home activity shelf

Each adult profile can enable the Scouts shelf under Settings → Appearance. It is off by default and can be placed before or after the video rows. It loads independently of the video feed and shows active or waiting agents, then recent real discoveries when the team is idle. The shelf links to Agent Activity and Scout Report; it is unavailable to child profiles. The current placement control chooses the beginning or end of the video rows rather than arbitrary ordering among individual rows.

### Narration modes (P2)

Every Hub agent narrates real milestones in three deterministic voices: professional, friendly (default) and playful.
Each curator has a tone column (Settings → narration voice in the curator editor; a new `tone` column is added to the
`curator` table by the existing additive startup migration). Source Scout and the Briefing Writer share a per-profile
voice under Settings → Appearance → Research team voice (profile preference `hub_tone`). All voices state exactly the
same facts and numbers; only wording differs, and no voice claims work that did not happen. Variant choice is derived
from the run identifier, so a step never flaps between variants. Messages are narrated at emit time; events written
before a voice change keep their original wording. Unknown tone values fall back to friendly. Tests cover the full
milestone table across all voices, determinism, fallback, API validation and one narration-backed end-to-end run.

### Phone-to-TV pairing hardening

Registration (`POST /api/playback/targets`, capped at ten screens) generates a six-digit pairing code that is
listed nowhere public: senders pair by entering it in the "Play on" picker after selecting a screen, and the receiving
screen shows its code in the picker. Receivers registered before this feature recover their code from the heartbeat
response, which is generated by an additive `playbacktarget` (pair, rate) migration at startup. Commanding a screen
(`POST .../commands`) requires an unlocked profile that is paired with that screen. Child profiles pair and cast like
any other: they can already watch every video on their own device, so blocking them protected nothing. Receivers
report position and duration in each heartbeat; a `seek` command carries `position_s` and the receiver clamps it to
the video length. A JSON rate window on the target
row (30 commands / 10 s, serialized via BEGIN IMMEDIATE) bounds mailbox flooding. Five wrong pairing codes
within ten minutes lock pairing for that screen until the window passes, so the code cannot be guessed by
enumeration. All previous guarantees remain:
short-lived commands, acknowledgement-based delivery, hashed receiver credentials, online-window checks and per-profile
playlist ownership. Tests: 5 playback suites (registration/pair/delivery/offline credentials, sending scope and
playlist ownership, cap + rate limit, legacy-row code recovery, child blocks) besides the browser pairing test.

### Scout Report stories and richer home preview (P3)

Discovery stories (`bt/hub/stories.py`) are deterministic, factual retellings generated when a report is created, from
real persisted rows within the report period: watch-curator runs that kept finds (citing what was checked where, kept
counts, corroborating curators matching the same canonical item through real provenance, AI-ranking fallback or one
source issue with its actual error), Source Scout runs that validated sources (provider groups, sitemap leads),
fresh cited research reports, and high/critical alerts. Stories never report activity the rows do not support, at
most five per report, each with fact links (curator, finding, sources, alerts, report). They persist inside the
issue's JSON so report history keeps the wording as it was generated.

The Scout Report page renders a Stories section above discoveries. The opt-in home preview
(`GET /api/hub/scout-report/preview`) now reports the configured schedule (frequency, next automatic run) and counts
(findings, curator runs, attention = failed runs + new alerts, stories), and the home card covers the real cases:
disabled + `show_home` (nothing is shown), enabled with no report yet (first-report CTA), a current report (counts,
needs-attention line, the headline story or top finding with its link), and the next scheduled run. Child profiles
still receive no Hub data.

### Curator feedback from findings

Each "Found by" curator on an item page has "More like this" and "Less like this". The endpoint
(`POST /api/hub/items/{id}/curator-feedback`) learns up to three distinctive title words, skipping stopwords and words
the curator already follows (name, subject, keywords, queries, aliases), into `tuned_up` / `tuned_down` on the curator
(newest 20 kept; a word moves out of the opposite list). In runs, a matching item's keyword or AI score moves ±10 with
the reason shown ("Less like this: …"); opposite matches cancel. Feedback never lets an item without keyword matches past
the keyword gate and never excludes anything. The learned words are editable in the curator form.
