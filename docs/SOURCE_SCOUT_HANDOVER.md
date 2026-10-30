# Source Scout handover — 2026-10-30

The user asked to stop at a good point and leave a handover. This is a tested Source Scout foundation; the larger
Source Scout and research-team roadmap remains open. **Deployed 2026-10-30:** `10.0.10.73:6646` (public endpoint
`https://wc.tdgs.org`) runs `3668357` as image `watchcraft:3668357` (also tagged `latest`) for the API, worker and
downloader. See "Deployment record" below.

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

## Deployment record — 2026-10-30

- Local re-verification on `3668357`: backend 262 passed, 6 skipped; svelte-check 0 errors/warnings; production
  build; 7 Playwright tests (Scout, access, navigation) passed.
- Before rollout, prod API ran `880f732`; worker and downloader ran the `43f6230` image. Fresh online SQLite backup
  (integrity `ok`) plus data extras, `.env`/compose config, live source tree and running-image record:
  `/root/compose/watchcraft-backups/20261030T094200Z-pre-3668357`. Rollback image:
  `watchcraft:rollback-20261030T094200Z-880f732`; `watchcraft:43f6230` is also retained. Media directory
  `/mnt/local/localstorage/watchcraft` was not copied (unchanged by this release). `compose.host.yaml` checksum
  verified unchanged.
- Isolated restore of that backup ran `watchcraft:3668357` on `127.0.0.1:6699`, started twice: integrity `ok`,
  6 profiles / 4822 videos / 115 history rows / 17 categories preserved, `scoutplan` and `scoutrun` created.
- Isolated acceptance from the server (real egress), temporary profiles removed afterwards: lwn.net feed discovered,
  revalidated and attached to a curator; anthropic.com/news (no feed) produced an `html_listing` watcher with 35
  sample links, accepted to a curator; non-HTTPS rejected; `127.0.0.1` yields no suggestions; cross-profile run/plan
  access returns 404; plan create/pause/resume/delete; Scout milestones absent from global events; converting a
  PIN-protected adult to child disabled its plan and returned 403 for Scout plans and research. The disposable
  container and restored data were removed.
- Topic search was **not** tested: no SearXNG/Brave provider is configured in production (returns the configuration
  error as designed).
- Deployed with `docker compose -p watchcraft -f compose.yaml -f compose.watchcraft.yaml -f compose.host.yaml up -d
  --no-build --wait`: all containers healthy, local and public `/api/health` OK, live DB quick_check `ok` with Scout
  tables, `/sources` 200 locally and publicly, Scout API 200, deployed frontend bundle matches the local build, and a
  saved offline video served with HTTP 206 and decoded 3 s of distinct frames via ffmpeg.
- Rollback: `docker tag watchcraft:rollback-20261030T094200Z-880f732 watchcraft:latest` and rerun the compose command
  above (the new tables are additive and ignored by the old release). Restore the backup only if data must be reverted.
- Still not done: physical TV remote pass and a real topic-search provider test.

## Deployment record — Agent Activity, parallel agents, new logo (`dfc8ed2`, 2026-10-30)

- Backup before rollout (online SQLite `.backup`, integrity `ok`, plus data extras, config, source, running images):
  `/root/compose/watchcraft-backups/20261030T102317Z-pre-dfc8ed2`. Rollback image:
  `watchcraft:rollback-20261030T102317Z-3668357`.
- Isolated copy on `127.0.0.1:6699` ran `watchcraft:dfc8ed2` and was started twice: integrity `ok`, counts kept.
  Production profiles now have PINs and child profiles, so PINs were reset **on the disposable copy only**.
  - `/api/hub/team` returned the profile's curator and Source Scout.
  - `team.max_parallel` defaults to 1. A value of 9 is rejected (400), a non-admin profile cannot change it (403),
    and an administrator can set it to 2.
  - A manual curator run showed "running" and then "idle" with 7 new items out of 100.
  - Another profile could not see the curator, and a child profile got 403.
  - The copy and its container were removed.
- Production after the deploy:
  - API, worker and downloader all run `watchcraft:dfc8ed2` (`e3b389cc…`). Health checks pass locally and
    publicly, the database quick_check is `ok` and counts are kept.
  - `/team`, `/logo.png` and `/favicon.png` return 200, and the deployed bundle contains Agent Activity.
  - `team.max_parallel` is 1, a saved video decoded 207 frames, and the API logs show no tracebacks.
- Known test-order issue: `smoke.spec.ts` leaves a second profile, so `source-scout.spec.ts` then shows the profile
  picker when both run in one batch. Each passes when run alone.

## Deployment record — source monitoring (`ed95110`, 2026-10-30)

- Added bounded sitemap/index monitoring, page text revisions with before/after excerpts, Scout page-monitoring
  fallback, and the latest 20 source checks. Partial multi-query failures retain their errors. These are incremental
  features; shared polling, selector configuration, automatic repair, notifications and full index traversal remain open.
- Backend release suite: 276 passed, 6 skipped. The access-test fixture no longer starts a live scheduler while
  rebuilding test tables, resolving its background-thread teardown warning. Remaining warning: test-client deprecation.
- Frontend check/build passed. Ten disposable browser checks passed for monitoring, Scout, roles/PINs, desktop/TV/mobile
  navigation and Agent Activity. These do not establish hardware TV acceptance.
- Backup: `/root/compose/watchcraft-backups/20261030T121656Z-pre-monitoring` contains a verified SQLite backup, private
  data extras/configuration, previous source and rollback image record. `final-before-rollout.sqlite3` is the additional
  backup taken with the three application services stopped immediately before rollout. Media was unchanged and not copied.
- Restored the first backup into an isolated directory and initialized twice with the release image: all original table
  counts, profile checksum and PIN records were preserved; new revision/check tables were created. Only the disposable
  copy received QA profiles. Real server-egress acceptance passed for example.org page monitoring (including unchanged
  detection), LWN RSS ingestion, source check history, child rejection and Django's sitemap (50 items).
- Production API/worker/downloader run `watchcraft:ed95110`, image `c104af291d7f…`. Public health and local `/sources`,
  `/team`, `/tv`, provider registry and check-history APIs passed. Deployed assets include the monitoring UI. Saved media
  returned HTTP 206 and decoded 180 changing frames in three seconds. Six profiles, 4,822 videos, 115 history rows,
  17 categories/playlists and the profile checksum were preserved. Host GPU override was retained.
- Rollback: tag `watchcraft:rollback-20261030T121656Z-pre-monitoring` as `watchcraft:latest`, then run
  `docker compose -p watchcraft -f compose.yaml -f compose.watchcraft.yaml -f compose.host.yaml up -d --no-build --wait`
  from `/root/compose/watchcraft`. New tables are additive. Restore the stopped-service backup only if reverting data is
  necessary, with all application services stopped first.
- Physical TV remote acceptance and topic search with a configured SearXNG/Brave provider remain unverified.
