# WatchCraft — "Watching the Internet for you."

WatchCraft evolves BetterTube into a self-hosted hub that goes on the internet for you. TV (`/`, `/tv`) remains
the default and is unchanged. Design, route map, provider registry and API contract: [HUB.md](HUB.md).

## What exists now (schema v2)

| Section | Route | What works |
|---|---|---|
| TV | `/`, `/tv`, `/tv?mode=tv` | Existing video home. `?mode=tv` switches the browser to TV mode for media-center launch. |
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
- OSINT is limited to public sources; the UI states lawful/ethical use. Not enabled-by-default for restricted child profiles —
  child restrictions for hub content are **not implemented yet**; do not give children unsupervised hub access.

## Migration and rollback

`init_db` runs `bt.hub.migrate` inside the existing `BEGIN IMMEDIATE`. v1 → v2 adds curator columns, creates reusable
sources from each v1 curator, and copies v1 discoveries into items, provenance and saved state. The v1 `discovery`
table is kept untouched. Video tables are not modified. Back up first (maintenance menu); roll back by restoring the backup
with the previous checkout. Tested by `tests/test_curation.py::test_v1_database_migrates_to_v2`.

## Deploy on 10.0.10.73

1. Back up the existing installation with its maintenance menu before pointing this version at existing data.
2. Parallel install: `docker compose -p watchcraft -f compose.yaml -f compose.watchcraft.yaml up -d --build --wait`
   (Compose 2.24.4+). Never run two stacks against the same SQLite data directory.
3. `BT_PORT=6646` in `.env`. Optional: `WC_SEARCH_PROVIDER=searxng` and `WC_SEARXNG_URL=http://searxng:8080`
   (or configure in Settings → Search & sources). SearXNG must have the JSON format enabled.
4. Install `deploy/traefik-wc.yml` as `/opt/traefik/dynamic/wc.yml` on 10.0.10.60.
5. Media center: point the TV browser at `https://wc.tdgs.org/tv?mode=tv`.

## Not implemented yet (honest list)

Admin-only gating of settings writes (any unlocked profile can change the search provider, as with the LLM URL today), semantic search/embeddings, child-profile restrictions for hub content, advanced cron schedules and explicit run
cancellation, notification channels and quiet hours, phone→TV handoff, custom sidebar ordering (only pinned
collections/topics), curator config import (export exists), Reddit/Mastodon/Stack Exchange/OSV/vendor advisories,
recipe/pattern site-specific adapters beyond JSON-LD, offline reading, per-task AI model selection.

## Verification of this iteration

- Backend: full pytest suite green (hub tests in `tests/test_curation.py`: isolation, multi-curator provenance,
  FTS, partial failures, crash recovery, alerts, briefings, research fallback, page extraction, v1→v2 migration).
- Frontend: `npm run check` 0 errors; production build OK.
- Playwright (`npm run test:e2e`): all pass except 4 specs that also fail on the previous commit
  (`smoke` keyboard-only, `stats` viewport ×2, `status-pill` hover). New: `navigation.spec.ts`, `hub.spec.ts`.
- Not done: live-provider network tests, real SearXNG/Brave, TV remote pass on hardware, Docker build, deployment.
