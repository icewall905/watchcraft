# Source Scout handover — 2026-10-30

The user asked to stop at a good point and leave a handover. This is a tested Source Scout foundation; the larger
Source Scout and research-team roadmap remains open. **The changes in this handover have not been deployed to
production.** The live server at `10.0.10.73:6646` still runs the previous code release `880f732` (Hub P0), with
its worker and downloader running. The public endpoint is `https://wc.tdgs.org`.

## What is implemented

- Sources has a Source Scout panel. A user can research an HTTPS site or a topic; topic searches use the existing,
  configured SearXNG/Brave web-search provider and give a configuration error if neither is enabled.
- Scout looks for advertised RSS/Atom/JSON feeds in HTML and HTTP Link headers, or a few common feed paths. Feeds
  are fetched and parsed before being suggested and are fetched and parsed again before approval. Empty, structurally
  valid feeds can be suggested; this does not claim that they currently contain items.
- When no working feed is found, a deterministic listing watcher can extract same-host links from semantic `article`
  elements. It is suggested only if it actually extracted sample links. This is a deliberately limited fixed rule,
  not a general CSS/XPath or AI-generated watcher.
- Sitemap references from HTML and robots.txt are shown as unverified leads, not claimed to be monitorable sources.
- Explicit approval adds a source to the existing profile-owned registry and may attach it to an existing curator.
  Existing curator scheduling and source health then handle monitoring. Scout does not automatically add sources.
- Runs, preview samples and milestones persist per profile. Milestones reuse `AgentEvent`, and are hidden from the
  legacy global video-agent events endpoint/stream to avoid leaking profile-specific Scout activity. A Scout plan can
  run daily or weekly, and can be paused, resumed or deleted. Jobs are claimed atomically and concurrent research is
  bounded. Converting a profile to child pauses its plans; Hub authorization blocks Scout endpoints for children.
- New tables `scoutplan` and `scoutrun` are additive, created by existing `SQLModel.metadata.create_all` on startup.
  Existing `AgentEvent` rows are used for activity; no existing table columns are changed. Runs interrupted for ten
  minutes are marked failed and allow new work. Research uses the existing hardened HTTPS/public-IP fetcher.

## Validation completed locally

- Complete backend suite: 261 passed, 6 skipped (before the final small feed-validation/interruption checks).
- The final targeted `tests/test_curation.py` and `tests/test_access.py` run passed 49 tests; it covers
  feed discovery and revalidation, HTML listing monitoring, JSON feeds, schedules, cross-profile isolation, and child
  restriction. A separate targeted run including setup passed 50 tests.
- Svelte check: zero errors and warnings. Production frontend build passed. Seven browser tests passed for Scout
  approval/history/schedule UI, child/admin access, and desktop/TV/mobile navigation.
- An earlier full-suite run from the repository root failed two subprocess imports due to the wrong working directory;
  running from `backend/` fixed that. A separate concurrent-first-start SQLite lock failure occurred once and did not
  recur in the following full or targeted runs. One existing Starlette/httpx deprecation warning remains.
- The browser Scout test mocks discovery responses; backend tests exercise discovery using fixtures. No real external
  website or provider acceptance test has been run for this new feature, and no physical TV test has been performed.

## Resume and deployment

1. Inspect `git status`, the branch and the current host state; do not overwrite any newer user changes.
2. Run `cd backend && .venv/bin/pytest tests/test_curation.py tests/test_access.py -q` and, if needed, the full
   backend suite (`.venv/bin/pytest tests -q`). Run `cd frontend && npm run check && npm run build`, then
   `npm run test:e2e -- source-scout.spec.ts access.spec.ts navigation.spec.ts --timeout 45000`.
3. Build the committed revision as a separate Docker image on `10.0.10.73`. Before production rollout, take a fresh
   backup of the current data and config, preserving the separate media directory, `compose.host.yaml` NVIDIA
   override and prior image. The earlier pre-Hub backup is under
   `/root/compose/watchcraft-backups/20261030T090143Z-43f6230`, but predates newer production activity.
4. Restore the fresh backup into isolated directories and run the new image against that copy. Confirm database
   integrity, existing profile/content counts, and creation of `scoutplan` and `scoutrun`; test startup twice.
5. Use a fresh isolated container on the real server for child/PIN and Scout acceptance. Test at least one real
   public feed and one real semantic article listing; use a configured search provider to test topic search if one is
   available. Confirm server egress, profile isolation, rejection/revalidation, curator attachment, and schedule
   pause/resume without altering production profiles. Dispose of the temporary test container/data afterward.
6. Deploy using `/root/compose/watchcraft/compose.yaml`, `compose.watchcraft.yaml`, and `compose.host.yaml` with
   project name `watchcraft`. Keep a rollback image and the fresh backup. Verify health, live Sources UI, actual
   playback and public proxy health; record the deployed code revision and update this handoff.

## Still to build

General declarative selectors and editing/version rollback, sitemap/page-change/API/browser watchers, source repair,
shared source polling, automatic adoption policies, AI relevance and investigation, source health analytics, and the
shared research-team experience. Scheduled Scout searches currently produce suggestions only, and there is no
automatic repair or unapproved source registration. These limitations are also listed in `docs/WATCHCRAFT.md` and
in the local `todo.md` (which is intentionally ignored by Git).
