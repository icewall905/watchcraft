# Source Scout handover — 2026-10-30


## Deployment record — basic reliability (`693fd02`, 2026-11-01)

- Covers `a8f7c81..693fd02`: scanned-PDF OCR integration, explicit OCR truncation notices,
  official NVD API recovery, browser rendering refused on every host, complete-word curator
  feedback, and the screen-forgetting pairing race. Tests now isolate background dispatch,
  compare structured migration versions, reset memory fixtures, select a profile explicitly,
  and check the intended report finding and loaded video card.
- Release verification: **515 backend tests passed, six skipped; all 128 browser tests passed**
  together in 5.9 minutes. Frontend check/build passed with the existing three CSS compatibility
  warnings. Independent review approved the fixes and acceptance checks.
- Backup: `/root/compose/watchcraft-backups/20261101T1515-basics-a65a79e` contains a verified
  online database, data extras, private configuration, previous source and a stopped-service
  `final-before-rollout.sqlite3`. Rollback tag: `watchcraft:rollback-20261101T1515-basics`.
  Final snapshot: six profiles, 7,012 videos, 200 content items and 200 capture jobs.
- The backup initialized twice with the final image on an isolated restore. A release candidate
  also passed two server starts against that copy with networking disabled. A real image-only PDF was read by the shipped OCR binaries
  (76 characters, no truncation); a failed NVD advisory recovered 1,078 characters through its
  official API. The restored profile checksum matched the backup. Disposable data and intermediate
  release images were removed afterward.
- Source copied with rsync **without `--delete`**. `.env` and the NVIDIA host override were
  unchanged. API, worker and downloader run image `sha256:cffebd09cb0f1139…`; `DEPLOYED_COMMIT`
  is `693fd02`. Public and local health pass, all three app containers have zero restarts, database
  integrity is `ok`, and profile/PIN records match the final stopped-service snapshot.
- Live acceptance: `/`, `/sources`, `/team`, `/settings` and `/tv` return 200; a saved video returns
  HTTP 206 and decodes 20 frames from a bounded range. Both tracked YouTube cases (`dQw4w9WgXcQ`,
  `BYVZh5kqaFg`) served video/audio range requests and decoded five video frames at 360p during
  validation. Those probes do not establish physical-device acceptance or eliminate intermittent
  upstream restrictions. Physical TV/phone checks were deferred at the user's request.
- Requeued 38 failed NVD captures with staggered initial due times. The original job state is
  archived as `nvd-retry-before.json` beside the deployment backup. **All 38 recovered:** all 44
  NVD items now have local text and complete jobs, with no NVD errors. A scheduled curator also
  found and captured 20 additional items. Final state: 220 items/vectors, 198 complete captures,
  13 failed and nine partial; all vectors have valid binary lengths and integrity is `ok`. The
  remaining failures include publisher access restrictions, a certificate failure, an unreadable
  page, a timeout and a page over the HTML size limit; those limits were not weakened.
- No schema change. Rollback to the retained `a8f7c81` image is database-compatible: tag
  `watchcraft:rollback-20261101T1515-basics` as `watchcraft:latest`, then run the existing three-file
  Compose command with `up -d --no-build --wait`, and restore the prior `DEPLOYED_COMMIT` marker.
  Restore the backed-up source before rebuilding the old release. Do not restore the database
  merely to revert this image, because that would discard subsequent discoveries and captures.


## Deployment record — semantic storage, bug fixes, news pictures, capture rescue (`a8f7c81`, 2026-11-01)

- Covers `30ed766..a8f7c81`, twelve commits: semantic vectors as float32 BLOB with migration v3 and
  `MAX_COMPARE` 20,000; six bug fixes (playing copies never evicted, card download status, live
  download progress, stacked Save bars, `httpx2`, slug words in curator feedback); news pictures and
  feed presentation; curator creation during Source Scout accept; capture rescue.
- Backend suite: 489 passed, 6 skipped. `test_sitemap_cursor_is_per_curator` failed in the full run and
  passes solo and per-module — the known B-012 flake, unrelated. Svelte check: zero errors, three CSS
  compatibility warnings. Browser tests: team, hub, content, source-scout, navigation, offline,
  settings-walk, category-playlist all passed.
- Backup: `/root/compose/watchcraft-backups/20261101T100114Z-pre-rescue` — online SQLite backup
  (integrity `ok`, 6 profiles, 6,856 videos, 177 content items, 6 curators, 9 sources, 177 vectors,
  177 capture jobs, schema v2) plus the three compose files, `DEPLOYED_COMMIT` and the env archive.
  Rollback image: `watchcraft:rollback-20261101T100114Z-30ed766` (`3c0e15824e0a`).
- Rollout used rsync **without** `--delete`; 53 source files changed, nothing removed. Image rebuilt
  (1.2 GB → 1.94 GB) and now carries chromium, tesseract-ocr and bubblewrap. All four containers
  healthy. `DEPLOYED_COMMIT` is `a8f7c81`; API, worker and downloader run `watchcraft:latest`
  (`sha256:43f9b5677b18`).
- Migration v3 ran on start: schema version 3, all 177 vectors stored as `blob`, zero rows whose
  length disagrees with `dim`, and every table count unchanged. `PRAGMA quick_check` is `ok`.
- Verified after rollout: health 200 locally and publicly; `semantic.available()` true with 177 of 177
  items indexed and a live search returning hits; `MAX_COMPARE` 20,000; `/hub/items/118/image` returns
  a real 2,902-byte PNG through the proxy; the deployed bundle contains the news `story-meta`, the
  "Recover unreadable pages" toggle and "New curator"; `local_library` kept `asset_max_mb` 4096 and
  gained `rescue: true`; no tracebacks.
- **`render` does not work in this container.** bubblewrap cannot create a mount namespace: the host's
  `/` is a shared mount and the container lacks `CAP_SYS_ADMIN`, so bwrap exits "Failed to make /
  slave". Making it work needs `cap_add: [SYS_ADMIN]` plus seccomp and apparmor unconfined — the
  capability that enables container escape, for one of seven rescue tools. Not granted.
  `sandbox_works()` probes once and `render` declines; the other six tools run. Recorded in
  `docs/SECURITY.md` and `compose.yaml`.
- Not verified: the hub UI flows were checked at the bundle and API-function level, not by clicking
  through as an administrator. Profile 1 is a child, so hub endpoints correctly return 403 for it, and
  real profile PINs were not touched.
- Rollback: `docker tag watchcraft:rollback-20261101T100114Z-30ed766 watchcraft:latest` and rerun
  `docker compose -p watchcraft -f compose.yaml -f compose.watchcraft.yaml -f compose.host.yaml up -d
  --no-build --wait` from `/root/compose/watchcraft`. The vector repack was **in place**, so the old
  image reads JSON text and would fail on the repacked rows: restoring
  `20261101T100114Z-pre-rescue/bt.sqlite3` is required, with the application services stopped first.


## Deployment record — role probes and embeddings URL (`30ed766`, 2026-11-01)

- Covers `43cc914..30ed766`. `/agent/llm-test` takes a role, so the worker and embeddings servers can be
  tested from Settings → AI instead of waiting for a run; the probe resolves the same endpoint, key and model
  the runtime would use. The embeddings probe posts one word to `/embeddings` and reports the vector size —
  dimensions are read from the response and stored per model, so there is nothing to configure.
- Fixed a real defect found while checking the live configuration: a pasted `.../v1/embeddings` was posted to as
  `.../v1/embeddings/embeddings` and answered 404, so semantic search could never have worked with that value.
  `semantic.base_url()` strips a trailing `/embeddings`, and the role probe uses the same normalisation.
- No schema change.
- Backend suite: 461 passed, 6 skipped, no failures. Svelte check: zero errors, three CSS compatibility warnings.
  Browser tests: settings-walk 14, llm-providers 3 and welcome 3 passed.
- Backup: `/root/compose/watchcraft-backups/20261101T075743Z-pre-role-tests` (online SQLite, integrity `ok`;
  config archive). Rollback image: `watchcraft:rollback-20261101T075743Z-43cc914`.
- Rollout used rsync **without** `--delete`; all four containers healthy. `DEPLOYED_COMMIT` is `30ed766`; API,
  worker and downloader run `watchcraft:30ed766` (`sha256:3c0e15824e0a…`). Health passes locally and publicly,
  no tracebacks after start.
- Live acceptance: the configured embeddings server (`http://10.0.10.73:8082/v1`, model `embeddinggemma`)
  resolves correctly and `semantic.available()` is true; the scheduler sweep embedded 32 items at 768 dimensions.

## Deployment record — three AI roles (`43cc914`, 2026-11-01)

- Covers `40aa983..43cc914`. The AI tab now groups Judge, Worker and Embeddings, each with its own optional server
  and key. Embeddings may point at their own endpoint: a separate server never receives the judge's key, so a typo
  cannot send one service's credential to another, and a key set without a separate server means "same server,
  different credential". Both keys are redacted and preserved across saves, and a server URL that is not http(s) is
  refused rather than saved. The worker and embeddings groups used a `.grid` class that was never defined, so the
  fields stacked full width; it is defined now. The welcome guide reuses this component, so it shows only the judge
  connection — onboarding should not ask for three endpoints.
- No schema change. `config.py` gains `embed_base_url` and `embed_api_key`, both empty by default.
- Backend suite: 456 passed, 6 skipped, no failures. Svelte check: zero errors, three CSS compatibility warnings.
  Browser tests: welcome 3, settings-walk 14 and llm-providers 3 passed. The full suite reports 122 passed and the
  five known B-011 flakes.
- Test fixes for the rename: llm-providers and welcome use the new labels, and the settings walk now puts the caret
  at the end of a text field before pressing a direction. A field with a value consumes Left/Right to move its
  caret, so the old harness left the caret mid-value and could never arrow out to the right.
- Backup: `/root/compose/watchcraft-backups/20261101T074531Z-pre-embed-endpoint` (online SQLite, integrity `ok`,
  6 profiles, 6,767 videos, 6 curators, 151 content items; config archive). Rollback image:
  `watchcraft:rollback-20261101T074531Z-40aa983`.
- Isolated restore on `127.0.0.1:6699`: health 200, no tracebacks, integrity `ok`. The copy was removed.
- Rollout used rsync **without** `--delete`; all four containers healthy. `DEPLOYED_COMMIT` is `43cc914`; API,
  worker and downloader run `watchcraft:43cc914` (`sha256:503bb3d32b03…`). Health passes locally and publicly, no
  tracebacks after start, the live settings show all three roles with the optional fields empty, and the served
  bundle carries the Judge/Worker/Embeddings labels.

## Deployment record — judge and worker models (`40aa983`, 2026-11-01)

- Covers `168c120..40aa983`, the last open task (T-058). One model did everything, so a big model was spent
  screening search results and summarizing captured text. `llm._cfg(role)` now resolves an endpoint per role: the
  judge keeps the existing `model`, `base_url` and `api_key`; the worker uses `worker_model`, `worker_base_url`
  and `worker_api_key`, each falling back to the judge's value when unset, so a single-model setup behaves exactly
  as before. `chat()` and `chat_json()` take `role="judge"` by default and `_request` resolves the endpoint from
  it. Screening, captured-text summaries and playlist keyword suggestions pass `role="worker"`; verdicts,
  planning, review, reports, briefings, meme explanations and ranking stay on the judge. Settings → AI gains the
  three worker fields, with the key redacted and preserved across saves, and pointing the worker at a new server
  without a key is refused rather than silently reusing the judge's. The existing "Context by model" table already
  breaks requests down by model, so the split is observable without new UI.
- No schema change: `models.py`, `hub/models.py`, `hub/migrate.py` and `db.py` are untouched. `config.py` gains
  three settings keys with empty defaults, so nothing changes until an administrator sets a worker model.
- Backend suite: 452 passed, 6 skipped, no failures. Svelte check: zero errors, three CSS compatibility warnings.
  Browser tests for the touched area: settings-walk 14 and llm-providers 3 passed.
- Backup: `/root/compose/watchcraft-backups/20261101T070050Z-pre-t058` (online SQLite, integrity `ok`, 6 profiles,
  6,743 videos, 6 curators, 151 content items; config archive). Rollback image:
  `watchcraft:rollback-20261101T070050Z-168c120`. Separate media volume preserved.
- Isolated restore on `127.0.0.1:6699` with the new image: health 200, no tracebacks, integrity `ok`. The copy was
  removed.
- Rollout used rsync **without** `--delete`; compose project `watchcraft` with `compose.yaml`,
  `compose.watchcraft.yaml` and `compose.host.yaml`; all four containers healthy. `DEPLOYED_COMMIT` is `40aa983`;
  API, worker and downloader run `watchcraft:40aa983` (`sha256:f3210c60bf1a…`). Health passes locally and
  publicly, no tracebacks after start, and the live settings show `model: currentmodel` with an empty
  `worker_model` — the unchanged single-model behaviour.


## Deployment record — P7 hardening (`168c120`, 2026-11-01)

- Covers `a0e551a..168c120`, completing P7. Mostly verification that had never been written down, plus one
  real defect the new guard found. `tests/test_recovery.py`: interrupted curator runs are failed and their lease
  released, a stale capture lease is re-claimed, a live one is left alone, and a job for a profile that lost
  access is failed rather than run. `tests/test_performance.py` counts SQL statements instead of timing and
  found the home feed issuing four statements per playlist, two of them redundant — `_watched_ids` is now cached
  per request and the home feed skips the ownership lookup it had already proved, so four became two.
  `tests/tv.spec.ts` covers TV mode (collapsed sidebar, small drawer, video search instead of the command
  palette, remote-only navigation, switching back). `tests/mobile.spec.ts` covers the phone bottom bar, no
  sideways scroll on five pages, 40px tap targets and top bar reachability. `tests/a11y.spec.ts` covers
  accessible names on seven pages, alt text, labelled fields, no positive tabindex, one h1 and a visible focus
  ring. `tests/pwa.spec.ts` covers the manifest fields, the maskable icon, every icon resolving as a real PNG,
  and the declared theme colour and viewport. `docs/SECURITY.md` records the trust boundaries, what each control
  covers, and the known limits stated plainly.
- No schema change in this range: `models.py`, `hub/models.py`, `hub/migrate.py`, `db.py` and `config.py` are
  untouched.
- Backend suite: 446 passed, 6 skipped, no failures. Svelte check: zero errors, three CSS compatibility
  warnings. Full Playwright suite: 123 passed and the five known B-011 flakes (`scout-report`, `team`,
  `watchers` ×3), which fail identically on a stashed clean tree. The 22 new browser tests all pass.
- Backup: `/root/compose/watchcraft-backups/20261101T044247Z-pre-p7` (online SQLite, integrity `ok`, 6 profiles,
  6,581 videos, 6 curators, 151 content items, 20 meme examples; config archive). Rollback image:
  `watchcraft:rollback-20261101T044247Z-a0e551a`. Separate media volume preserved.
- Isolated restore on `127.0.0.1:6699` with the new image: health 200, no tracebacks, integrity `ok`, 6 profiles.
  The copy was removed.
- Rollout used rsync **without** `--delete`; compose project `watchcraft` with `compose.yaml`,
  `compose.watchcraft.yaml` and `compose.host.yaml`; all four containers healthy. `DEPLOYED_COMMIT` is
  `168c120`; API, worker and downloader run `watchcraft:168c120` (`sha256:587f491e0312…`). Health passes
  locally and publicly, no tracebacks after start, and `/`, `/memes`, `/sources`, `/settings` and `/find` all
  return 200.


## Deployment record — P6 advanced (`a0e551a`, 2026-11-01)

- Covers `c4ce003..a0e551a`, completing P6. Notifications (`fad2d39`): alert delivery to a JSON webhook, a Home
  Assistant webhook and SMTP email, driven from the scheduler past a stored cursor so a restart neither replays
  nor loses alerts; one failing channel never blocks the others and a broken channel never fails a curator run.
  Settings → Notifications configures them with a Send test action, and the email password is redacted. Desktop
  notifications are opt-in per browser and poll the alerts endpoint; there is no push server. Source quality
  (`fad2d39`): discoveries, acceptance rate and last contribution counted from `CuratorDiscovery`, with a
  productive/thin/noisy/idle verdict beside health on the Sources page. Providers (`fad2d39`): dev.to (the first
  query word becomes a tag) and Lobste.rs stories; Bluesky and Reddit were evaluated first and both answer 403 to
  datacenter IPs, so neither is offered. Relevance and search (`a0e551a`): profile-wide taste learned from saved,
  favourite, rated and dismissed items nudges a score ±5 and never past the keyword gate; semantic search indexes
  items through an optional embeddings model and powers Search by meaning in Find.
- Schema: one additive table, `itemvector` (item_id + model primary key, vector, dim). No existing column changed.
- Backend suite: 439 passed, 6 skipped, plus the known B-012 flake in the full run (passes solo). Svelte check:
  zero errors, three CSS compatibility warnings. Browser tests for the touched areas: settings-walk 20 (now
  covering the Search and Notifications tabs), hub 4, source-scout 1 passed.
- Backup: `/root/compose/watchcraft-backups/20261101T041605Z-pre-p6` (online SQLite, integrity `ok`, 6 profiles,
  6,543 videos, 6 curators, 151 content items, 20 meme examples, 0 alerts; config archive). Rollback image:
  `watchcraft:rollback-20261101T041605Z-c4ce003`. Separate media volume preserved.
- Isolated restore on `127.0.0.1:6699` with the new image: health 200, no tracebacks, integrity `ok`, counts
  unchanged, and `itemvector` created. The copy was removed.
- Rollout used rsync **without** `--delete`; compose project `watchcraft` with `compose.yaml`,
  `compose.watchcraft.yaml` and `compose.host.yaml`; all four containers healthy. `DEPLOYED_COMMIT` is `a0e551a`;
  API, worker and downloader run `watchcraft:a0e551a` (`sha256:ac125099bfb6…`). Health passes locally and
  publicly; no tracebacks after start; `itemvector` exists on production.
- In-container acceptance (the hub and admin routes need a PIN-unlocked profile, and four child profiles exist):
  `semantic.available()` reports "Set an embeddings model under Settings → AI." with 151 items and 0 indexed,
  `notify.channels()` reports every channel off, and `notify.dispatch()` skips with "no channels configured" —
  the honest unconfigured state rather than a silent failure.


## Deployment record — screen management and Meme Radar autonomy (`c4ce003`, 2026-11-01)

- Covers `c72081b..c4ce003`. Screen management (`e3619bf`): administrators remove a screen from the sender
  side, revoke one profile's pairing, and rotate a pairing code; Settings gains a Screens tab and the Play on
  picker gains "Forget this screen". Deleting a profile drops its pairings and any command it left in a
  screen's mailbox. Meme Radar (`28ccbf1`, `292f85d`, `67e4500`, `c4ce003`): the analysis sweep runs at the top
  of every scheduler tick instead of behind the agent-slot gate, `/memes` gains a Refresh radar action, a meme
  curator with no working public source probes public Mastodon instances with its own queries and creates or
  re-points its source, a run whose every item was rejected for its content type now says so instead of
  reporting `ok`, the curator form offers Mastodon as a source, and meme cards show the whole picture with a
  quiet caption. Image memes are analyzed as soon as their image lands, like clips already were.
- No schema migration: `models.py`, `hub/models.py`, `hub/migrate.py` and `db.py` are unchanged in this range.
- Backend suite: 424 passed, 6 skipped. Svelte check: zero errors, three CSS compatibility warnings. Browser
  tests for the touched areas: memes 4, hub 3, local-library 1, playback 2, settings-walk 14 passed. The full
  Playwright suite reports 96 passed and the six known B-011 flakes (`scout-report`, `team`, `watchers`), which
  fail identically on a stashed clean tree.
- Backups: `/root/compose/watchcraft-backups/20261101T032749Z-pre-meme-radar` (online SQLite, integrity `ok`,
  6 profiles, 6,507 videos, 6 curators, 131 content items, 2 playback targets, 8 sources; config archive) with
  rollback image `watchcraft:rollback-20261101T032749Z-c72081b`, and
  `/root/compose/watchcraft-backups/20261101T034552Z-pre-meme-cards` with rollback image
  `watchcraft:rollback-20261101T034552Z-67e4500`. Separate media volume preserved.
- Isolated restore of the first backup on `127.0.0.1:6699` with the new image: health 200, no tracebacks,
  integrity `ok`, counts unchanged. The copy was removed.
- Rollout used rsync **without** `--delete`; a before/after listing showed nothing missing. Compose project
  `watchcraft` with `compose.yaml`, `compose.watchcraft.yaml` and `compose.host.yaml`; all four containers
  healthy. `DEPLOYED_COMMIT` is `c4ce003`; API, worker and downloader run `watchcraft:c4ce003`
  (`sha256:e5c457c79505…`). Health passes locally and publicly; no tracebacks after start.
- Live acceptance on production: the Meme Tracker (curator 4) ran with zero sources, the autopilot added
  `https://mastodon.social`, and the run collected 20 memes from 281 source items with no errors; its timeline
  shows `source_repaired`. The next sweep analyzed the 16 image memes that were waiting, leaving 20 examples and
  2 counted community trends. No profile PINs were read or changed; the run was triggered in the container
  because the owning profile is PIN-protected.


## Deployment record — meme videos and live Mastodon (`c72081b`, 2026-10-31)

- Covers `51b5666..c72081b`: local MP4/WebM meme playback with native controls and seeking, capture status
  refresh/retry, 25 MB/three-minute clip limits, decoded-frame validation with network protocols disabled,
  exact video identity rebuilt after late capture, and authenticated GET/HEAD/single-range video assets.
  Reuses existing asset tables and storage cap; no schema migration.
- Mastodon ingestion validates the original HTTPS source, accepts public original posts only, supports instance
  and explicit hashtag URLs without broadening the latter, selects playable attachments with separate posters,
  and reports instances requiring authenticated timeline access. Independent provider and video reviews approved.
- Full backend suite: 412 passed, 6 skipped. Final video-specific regression run: 5 passed. Svelte check: zero
  errors, three CSS compatibility warnings; production build and six browser tests passed, including actual MP4
  playback, seeking and drawer cleanup.
- Backup: `/root/compose/watchcraft-backups/20261031T192708Z-pre-meme-video`, including verified SQLite,
  data extras and source/config archive, plus the immediate pre-rollout DB. Rollback image:
  `watchcraft:rollback-20261031T192708Z-51b5666`. Separate media volume preserved.
- Isolated restored backup started twice with integrity `ok` and unchanged original counts: 6 profiles,
  6,157 videos, 3 curators, 69 content items and 1 item-state row. Background work was disabled on the copy.
  A temporary Meme Tracker on the production host collected 30 real public Mastodon posts; a second run added
  zero duplicates. A real 83,978-byte clip was downloaded, frame-validated and served with HTTP 206. Real image
  captures produced a counted, sparse `newly_observed` trend. No sources were added to real profiles.
- API, worker and downloader use image `88052757…` (`watchcraft:c72081b`, also `latest`);
  `DEPLOYED_COMMIT` is `c72081b`. Local/public health and DB quick check passed; host override and profile PIN
  hashes matched their pre-rollout values. Existing route/API and saved-video playback checks passed.


## Deployment record — P5 Meme Radar (`51b5666`, 2026-10-31)

- Covers `b5a9358..51b5666`: Meme Radar, Mastodon ingestion, image/text identity and deduplication,
  visual format groups, counted trends, save/follow/hide, similar examples and optional AI explanations.
  Review corrected a Cartesian trend query, daily/weekly baseline units, rolling-window sample counts,
  dropped AI context and scalar title handling, filter loading/request loops, sensitive image metadata/previews,
  and profile visibility for duplicate items and format/trend actions. Four new tables are additive.
- Final full backend run: 390 passed, 6 skipped, two scheduler-related test failures (sitemap cursor and shared
  polling). Both passed in an isolated rerun alongside Meme Radar: 25 passed. These suite races are tracked in
  the vault. Svelte check: zero errors, three CSS compatibility warnings; production build passed; five browser
  tests passed, including populated filters, empty topics and sensitive previews.
- Backup: `/root/compose/watchcraft-backups/20261031T191525Z-pre-p5`, including verified online SQLite,
  data extras and source/config archive, plus `immediately-before-rollout.sqlite3`. Rollback image:
  `watchcraft:rollback-20261031T191525Z-b5a9358`. Separate media volume preserved.
- Restored backup started twice with integrity `ok`, preserving 6 profiles, 6,157 videos, 3 curators, 69 content
  items and 1 item-state row. Scheduling was disabled on the copy. Disposable profiles and five image fixtures
  verified grouping, exact trend counts, save/follow, similar examples, profile isolation and child denial.
  The acceptance container was removed.
- API, worker and downloader run image `a350a482…` (`watchcraft:51b5666`, also tagged `latest`);
  `DEPLOYED_COMMIT` is `51b5666`. Host override and all profile PIN records matched their pre-rollout hashes.
  Local/public health, public `/memes`, live Meme Radar APIs and existing routes passed; DB quick check `ok`.
  A saved video returned HTTP 206 and decoded 180 frames with 180 distinct hashes in three seconds.


## Deployment record — P4 content experiences (`b5a9358`, 2026-10-31)

- Covers `7ea4edc..b5a9358`: Discover and Find filtering, conservative news grouping, topic organization,
  Library descriptions and notes, structured recipe/pattern details, cited reports and briefings, alert filters,
  and an inactive research follow-up curator draft. No database migration was required.
- Backend: 375 passed, 6 skipped; Svelte check and production build passed; four Hub, Source Scout and access
  browser tests passed. The known sitemap test race appeared in an earlier full run and passed on the final run.
- Fresh online SQLite backup with integrity `ok`, data extras and source/config archive:
  `/root/compose/watchcraft-backups/20261031T173159Z-pre-b5a9358`. Rollback image:
  `watchcraft:rollback-20261031T173159Z-7ea4edc`. The separate media volume was not copied.
- An isolated restored copy started twice with integrity `ok`; 6 profiles, 6,068 videos, 3 curators, 69 content
  items and 1 item-state row were preserved. Scheduled work was disabled on the copy. P4 routes and profile-scoped
  API checks passed using a temporary copy-only profile; the disposable container was removed.
- Production API, worker and downloader run `watchcraft:b5a9358` (image `964efcad…`); the host override checksum
  matched, local and public health passed, P4 routes returned 200, and database quick check was `ok`. Live Find
  rendered its new filters and returned content results. A saved video returned HTTP 206 and decoded 207 frames
  with 206 distinct hashes. Physical TV acceptance remains tracked separately.

The user asked to stop at a good point and leave a handover. This is a tested Source Scout foundation; the larger
Source Scout and research-team roadmap remains open. **Deployed 2026-10-31:** `10.0.10.73:6646` (public endpoint
`https://wc.tdgs.org`) runs `c72081b` as image `watchcraft:c72081b` (also tagged `latest`) for the API, worker and
downloader. See "Deployment record" below. Pending work is tracked outside the repo (see the local `AGENTS.md`).

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

## Deployment record — curator feedback (`7ea4edc`, 2026-10-31)

- Covers `8a9df18..7ea4edc`: "More/Less like this" on findings tunes curator scores; adds `curator.tuned_up` and
  `curator.tuned_down` (JSON, default `[]`) through the additive hub migration.
- Backup (online SQLite `.backup`, integrity `ok`, data extras, config, source, running images):
  `/root/compose/watchcraft-backups/20261031T165936Z-pre-7ea4edc`. Rollback image:
  `watchcraft:rollback-20261031T165936Z-2cf0445` (`3a2cd13f…`, the image production was running). The old release
  ignores the two new columns.
- Isolated copy on `127.0.0.1:6699` from the backup: started twice, no tracebacks; both columns added with `[]` on all
  3 curators; feedback on the newest real finding returned 200 and learned two title words. The copy was removed.
- Rollout with rsync without `--delete`; nothing missing from the live directory.
- Production: API, worker and downloader run `watchcraft:7ea4edc` (`c9a54eb3…`); NVIDIA override checksum matches;
  health passes locally and publicly; quick check `ok`; columns present; `/`, `/curators`, `/team` return 200; no errors.

## Deployment record — discovery stories, child casting, Play on seeking (`2cf0445`, 2026-10-31)

- Covers `1bdeae1..2cf0445`: discovery stories and richer Scout Report home preview (`153655b`, review fixes
  `dea6042`); child profiles pair and cast again, receivers report duration, senders seek (`2cf0445`).
- Backup (online SQLite `.backup`, integrity `ok`, data extras, config, source, running images):
  `/root/compose/watchcraft-backups/20261031T161219Z-pre-2cf0445`. Rollback image:
  `watchcraft:rollback-20261031T161219Z-64d2b70` (`0e08cc92…`, the image production was running).
- Isolated copy on `127.0.0.1:6699` from the backup: started twice, no tracebacks. A child profile listed both screens,
  paired a test screen and sent a seek the receiver picked up; a weekly report built three stories from real runs and a
  research report. The copy was removed.
- Rollout with rsync without `--delete`; nothing missing from the live directory.
- Production: API, worker and downloader run `watchcraft:2cf0445` (`3a2cd13f…`); NVIDIA override checksum matches;
  health passes locally and publicly; quick check `ok`; `/`, `/sources`, `/team`, `/scout-report` return 200; no errors.

## Deployment record — provider metrics, pairing lockout (`64d2b70`, 2026-10-31)

- Covers `f514f64..64d2b70`: source provider metrics and run durations in Team timelines (`e3125cd`, `b7e9b65`);
  pairing lockout after five wrong codes, pairing form submit fix, secure legacy codes, empty details hidden (`64d2b70`).
- Backup (online SQLite `.backup`, integrity `ok`, data extras, config, source, running images):
  `/root/compose/watchcraft-backups/20261031T150002Z-pre-64d2b70`. Rollback image:
  `watchcraft:rollback-20261031T150002Z-f514f64` (`9cf874c2…`, the image production was running).
- Isolated copy on `127.0.0.1:6699` from the backup: started twice, no tracebacks, 6 profiles, both existing screens
  keep their codes. A test screen refused five wrong codes (403) and then the right one (429, locked); Team returned
  4 agents with 20 of 35 timeline steps carrying source metrics and run durations. The copy was removed.
- Rollout used rsync **without** `--delete`, so untracked operational files in the live directory stay; a before/after
  listing showed nothing missing. Files deleted from git must now be removed by hand (none in this range).
- Production: API, worker and downloader run `watchcraft:64d2b70` (`0e08cc92…`); NVIDIA override checksum matches;
  health passes locally and publicly; quick check `ok`; `/`, `/sources`, `/team` return 200; no errors after start.

## Deployment record — search relevance screen, playback pairing (`f514f64`, 2026-10-31)

- Covers `40a9025..f514f64`: off-topic title screen before research, skipping re-uploads of rejected titles,
  subject-anchored planner queries (`287faa0`, `f514f64`); playback sender pairing codes and child restrictions
  (`2618535`, `9dd082e`, `69f2174`); dead downloads response shapes removed (`9e3ac7a`).
- Backup (online SQLite `.backup`, integrity `ok`, data extras, config, source, running images):
  `/root/compose/watchcraft-backups/20261031T133341Z-pre-69f2174`. Rollback image:
  `watchcraft:rollback-20261031T133341Z-40a9025` (`ebfce570…`, the image production was running).
- Isolated copy on `127.0.0.1:6699`, first with `69f2174`, then from a fresh restore with `f514f64`; started twice
  each, no tracebacks, quick check `ok`, 6 profiles and 5,844 videos kept. Playback targets gained `pair`/`rate` and
  both existing screens received 6-digit pairing codes. `/`, `/sources`, `/team` returned 200.
- Acceptance with real YouTube searches and the production LLM on the copy: "cat leap" dropped two parkour
  tutorials; "wintergatan marble machine" dropped six music-only/reaction uploads for the engineering playlist;
  on-topic results ("unlikely animal friends", a cat riding a Roomba) were kept. The first copy showed the judge
  itself sometimes writes `off-topic:` red flags, so `f514f64` identifies screen rejections by their missing score.
  The copy and its data were removed.
- Production: API, worker and downloader run `watchcraft:f514f64` (`9cf874c2…`); NVIDIA override checksum matches;
  health passes locally and publicly; quick check `ok`; no tracebacks after start.
- The rsync rollout with `--delete` removed nine untracked operational files from the live directory (incident
  backups, deploy notes); they were restored from the backup's `source.tar.gz`. Future rollouts must exclude them
  or skip `--delete`.

## Deployment record — P1 watchers, narration modes, planner search yield (`40a9025`, 2026-10-31)

- Covers `c4ddd77..40a9025`: planner search yield, seed commas, narration modes and fixes, P1 watcher rules, sitemap
  tracking, page diffs and alerts, source health and repair. Production previously ran `28de7fd` (image `fd0fdee2…`).
- Backup before rollout (online SQLite `.backup`, integrity `ok`, plus data extras, config, source, running images):
  `/root/compose/watchcraft-backups/20261031T103738Z-pre-0c71555`. Rollback image:
  `watchcraft:rollback-20261031T103738Z-28de7fd` (the image production was running).
- Isolated copy on `127.0.0.1:6699`, started twice, first with `0c71555` and then, after a fix, from a fresh restore
  with `40a9025`. Quick check `ok`, 6 profiles and 5,746 videos kept; the new columns and tables were added. PINs were
  reset **on the disposable copy only**. Acceptance with real server egress (`p1_accept.py` in the backup directory):
  - All existing sources still test successfully with default rules.
  - An invalid selector is rejected. Repair on Debian News proposed two tested rule sets. Saved rules passed a live
    test (4 news items), and the rule history kept "original" and "repair".
  - WordPress news sitemap: the first curator read gave 30 new items out of 50, the second gave 0, and 1,242 URLs are
    tracked. A Debian page watcher with a content area tested successfully.
  - Another profile gets 404 for the rules and a child profile gets 403. The source list shows health.
  - The first copy showed the RDAP lookup as broken because a lookup may rightly find nothing. `40a9025` judges only
    list-style sources on empty results; the fresh copy then showed it healthy. Web search showed degraded from real
    earlier failures.
  - The copy and its container were removed.
- Production after the deploy:
  - API, worker and downloader run `watchcraft:40a9025` (`ebfce570…`); compose files unchanged and the NVIDIA override
    checksum matches. Health checks pass locally and publicly; quick_check `ok`; 6 profiles, 5,746 videos, 8 sources.
  - `/sources` and `/team` return 200 and the deployed bundle contains the watcher rules UI. Hub APIs refuse requests
    without the profile PIN (403), as intended.
  - A saved video served HTTP 206 for a range request and its first 4 MB decoded 537 frames. No tracebacks in the API
    or worker logs.
- The new dependencies (beautifulsoup4, soupsieve) are in the image. Selector rules run in a short-lived child
  process (`python -m bt.hub.watchers`) inside the API container.

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

## Deployment record — shared polling (`96ec4de`, 2026-10-30)

- Scheduled feed providers now share a persisted raw response for five minutes across curators/profiles, with each
  curator's scoring and discoveries separate. Query providers are excluded. Manual runs, tests and Scout acceptance
  force freshness. Thirty-second failed-response reuse prevents retry storms; history labels reuse with the original
  fetch time. Capacity: 256 completed endpoints, two MB each, plus active leases. Slow checks renew their database
  lease; abandoned leases expire after three minutes. Waiting checks stop after 190 seconds.
- Backend: 282 passed, 6 skipped; frontend check/build passed; ten browser checks passed. Coverage includes independent
  cross-profile scoring, concurrent one-fetch behavior, persisted reuse in another interpreter, forced refresh, expired
  success/failure handling, lease renewal/interruption/recovery and storage bounds.
- Backup and rollback: `/root/compose/watchcraft-backups/20261030T123107Z-pre-shared-polling`, rollback image
  `watchcraft:rollback-20261030T123107Z-pre-shared-polling`. Includes verified online backup and final stopped-service
  backup `final-before-rollout.sqlite3`, configuration, data extras and previous source. Media was unchanged and not copied.
- Isolated restore initialized twice: all original table counts, profiles and PIN records preserved; `sourcepoll` created.
  Real public page/RSS/sitemap checks passed. Two real feed requests performed one network fetch with equal original
  timestamps. The disposable copy received QA profiles; production profile/PIN data was unchanged.
- API, worker and downloader deployed as `watchcraft:96ec4de`; six profiles, 4,822 videos, 115 history rows, 17 categories
  and profile checksum preserved. Host GPU override retained. Production health, new schema, source APIs/assets and
  saved-video HTTP/decode acceptance verified after rollout.
- Rollback: tag the rollback image as `watchcraft:latest` and run the existing three-file WatchCraft Compose command
  above with `up -d --no-build --wait`. Restore the stopped-service backup only when reverting data is necessary.
- P2 audit: existing team cards, deterministic narration, journals, Scout milestones, completed-run summaries and idle
  recent discoveries are implemented. Unified curator step timelines, richer preview/diagnostic UI, narration modes and
  the optional home shelf remain open, followed by Scout Report, Meme Radar and the rest of P3–P7.

## Deployment record — curator timelines and polling race fix (`e37d0e2`, 2026-10-30)

- Curator runs now emit real milestones into the existing profile-owned event store for source checks, shared-fetch reuse,
  mission matching, optional ranking/report writing and completion. Agent Activity expands the latest 12 milestones,
  including technical errors; up to 80 are retained per curator. Existing runs are not backfilled. Scout events use the
  same presentation. Curator/profile deletion removes private events, and both global video-event routes exclude them.
- Corrected a concurrent-poll edge case: freshness was calculated before obtaining the DB lock, so a response completed
  during the lock wait could look newer than the clock sample and trigger another fetch. Freshness now uses time read
  after locking. Regression coverage holds the lock while a response is published, then verifies that waiting reuses it.
- Full backend suite: 284 passed, 6 skipped. Ten release browser checks passed, plus a final team-layout check and seven
  focused polling tests. The team screenshot was visually inspected with expanded diagnostics. A live blocked-fetch
  test checks current progress, other-profile/global-stream isolation and true completion after the source returns.
- Backup: `/root/compose/watchcraft-backups/20261030T124046Z-pre-timelines`; previous image:
  `watchcraft:rollback-20261030T124046Z-pre-timelines`. Original counts/profile/PIN records survived two isolated
  initializations. Real page/RSS/sitemap checks, shared reuse and actual curator timeline/privacy acceptance passed.
- Production API/worker/downloader run `watchcraft:e37d0e2` (`09b759193f1f…`). Six profiles, 4,822 videos, 115 history
  records, 17 categories and the profile checksum were preserved. Public health, provider/history APIs, deployed
  monitoring/timeline assets and saved-video HTTP 206 with 180 changing decoded frames passed. GPU override retained.
- **Rollback compatibility:** previous images do not filter the new `hub_curator` event kind. Use the protected helper
  `python3 /root/compose/watchcraft-backups/20261030T124046Z-pre-timelines/rollback.py` for this rollback. It stops the
  application services, archives the entire current DB as `before-rollback.sqlite3`, removes private curator events
  before starting the old image, and restores the deployed-revision marker. Discoveries and profile state are retained.
  Automatic rollout failure uses the same privacy cleanup. Avoid an image-only rollback to pre-timeline releases once
  these events exist; the earlier image-only instructions describe historical releases.
- Disposable validation container/data were removed after acceptance. Roadmap items still open include detailed
  briefing milestones, richer finding previews, narration modes, optional home shelves, Scout Report and Meme Radar.

## Deployment record — discovery previews and briefing progress (`3f91e6d`, 2026-10-30)

- Recent discoveries now expand into a bounded source summary, source/publisher and timestamps, the curator's separate
  note, an internal finding link and the canonical original URL. Missing summaries are labeled; no new summary is
  generated. The API retains profile-owned discovery provenance and source ownership in the join.
- Briefing Writer records gathering/selection, actual AI-writing attempts or source-linked fallback, saving and completion
  in the existing private event store, retaining 80 events per briefing and displaying the latest 12 for its selected run.
  Progress is visible while generation runs, completion follows the issue commit, and deletion removes briefing events.
  AI writing now occurs outside the database session; profile access is checked again before saving. Failure narration
  does not assume an issue was never saved. Existing private event classification is retained for rollback compatibility.
- Verification: 286 backend tests passed, six skipped; frontend check/build clean. Ten browser checks passed across the
  acceptance batch and corrected team-test rerun; final expanded previews/progress screenshot visually inspected.
  Blocked-writer integration coverage verifies live progress before saving, exact source references afterwards, other-
  profile isolation and omission from shared streams. AI-disabled generation records fallback, with no invented AI step.
- Backup: `/root/compose/watchcraft-backups/20261030T130141Z-pre-previews`; rollback image:
  `watchcraft:rollback-20261030T130141Z-pre-previews` (previous application `e37d0e2`). Includes verified online and final
  stopped-service databases, protected data extras/config and source. Media was unchanged and not copied.
- Restored-copy initialization passed twice with all original counts, profile checksum and PIN records preserved.
  Real page/RSS/sitemap, shared-fetch reuse, evidence preview, source-linked briefing fallback/saved issue and milestone
  isolation checks passed on the final image. AI was disabled on the disposable copy only, leaving production settings
  untouched. The disposable container/data were removed after acceptance.
- API/worker/downloader run `watchcraft:3f91e6d`. Production health, monitoring APIs and deployed preview/timeline assets
  passed; saved media returned HTTP 206 and decoded 180 changing frames. Six profiles, 4,822 videos, 115 history rows,
  17 categories/playlists and profile checksum were preserved; host GPU override retained.
- Rollback to the retained `e37d0e2` image is compatible with the private event classification: tag the rollback image as
  `watchcraft:latest`, then run the existing three-file Compose command with `up -d --no-build --wait`. Reverting to a
  pre-timeline image still requires the private-event cleanup described in the earlier timeline deployment record.
