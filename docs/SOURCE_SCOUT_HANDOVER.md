# Source Scout handover — 2026-10-30

## Deployment record — immediate Shorts picker and visible exit (`eb9ae84`, 2026-11-03)

- Ships `84408b5` + `eb9ae84`: a five-minute memory-only feed cache scoped to profile/session; first visits seed profile-owned feed names from loaded categories while local availability is checked; counts remain explicitly unknown until refreshed. Background polling no longer supersedes slow pending requests. Batched scalar availability/stats queries avoid loading video transcripts/comments; playback still validates local files. Back to WatchCraft stays visible in picker/loading/playback/error states, with Feeds during playback.
- Validation: 794 backend tests passed, six skipped; final 21 Shorts browser checks passed, covering cached revisits, slow polling, profile/PIN isolation, provisional choices, authoritative empty replacement and exit controls. Frontend check/build passed (three existing CSS warnings); independent review approved; phone layout inspected at 390×600. Live availability timing varied from warm subsecond to a cold 7.8 s, so picker visibility is decoupled from that check rather than promising fixed I/O latency.
- Backup `/root/compose/watchcraft-backups/20261103-shorts-cache` retains original and fresh pre-picker databases and source. Rollbacks `watchcraft:rollback-20261103-shorts-cache` (69e7ec4) and `watchcraft:rollback-20261103-shorts-picker` (84408b5). Both isolated restored copies initialized with integrity/counts/profiles/PINs preserved and started twice successfully.
- Deployed `eb9ae84`; API/worker/downloader image `sha256:f82fd0eba54b7cbf80aa6e416cd767b0498e93ea5d41c366c4f9f2b1cb291034`, zero restarts. Local/public health and production integrity/configuration checks passed; new picker bundle present. Live Back to WatchCraft navigation returned to the main interface; actual adult feed rendering remains covered by isolated browser fixtures rather than entering real PINs.

## Deployment record — visible sidebar collapse toggle (`69e7ec4`, 2026-11-03)

- The sidebar collapse control now stays visible at the top, with `<<` to show icons only and `>>` to restore labels. Existing browser preference persists across reloads. Frontend check/build passed (three existing CSS warnings); both sidebar/TV browser regressions passed, including compact layout, reload persistence and expansion. Live public TV page was visually checked after collapsing.
- Backup `/root/compose/watchcraft-backups/20261103-sidebar`; rollback `watchcraft:rollback-20261103-sidebar` retains `d82a31c`. Isolated restored initialization preserved all table counts, profiles and PINs; service started twice and health/settings routes passed.
- Deployed `69e7ec4`, image `sha256:4b3e6e49545ed4817dd56674d634b5b377787dd63c500cd086a335376aae0429`; API/worker/downloader running with zero restarts. Local/public health, database integrity and configuration checks passed.

## Deployment record — local news archives (`d82a31c`, 2026-11-03)

- News story pages show archived text, AI summaries and uncropped locally stored images; the image opens at full size. Source titles lead to local archive details. Capture prefers fuller structured article text, rejects blank summaries, retains full social text, saves original social images, and follows Lemmy news article links while preserving post text and prior archives on failed retries.
- Verification: 793 backend tests passed, six skipped; final focused archive suite 69 passed; frontend check/build passed (three existing CSS warnings), six news/Hub browser checks plus two isolated renders of the actual story 57 content at desktop/phone widths with no overflow. Independent review approved.
- Backup `/root/compose/watchcraft-backups/20261103-archive`; rollback image `watchcraft:rollback-20261103-archive`. Isolated restored copy started twice with network disabled, integrity/profile/PIN checksums and content counts preserved; only isolated scheduler audit rows were added.
- Deployed `d82a31c`; API, worker and downloader run image `sha256:a26cac4f83b820d9c697564ea31f691fd1601d118e09bd0fcf24a0d14e1aecb6`, healthy with zero restarts. Local/public health and affected routes 200; new reader bundle present, production integrity/profile/PIN checksum and configuration unchanged. Story 57 retains 3,147 text characters, AI summary and an 80,848-byte WebP screenshot decoded at 972×717. Queued 125 existing captures for original-image upgrades; completion is background work.
- Actual public-story content was rendered on an isolated test server. The live browser session only had unprotected profiles with Hub disabled; real adult-profile UI acceptance remains in the vault.

## Deployment record — master curator scout card (`41ded70`, 2026-11-03)

- The Home scouts shelf linked the master curator (no curator id) to `/curators/null`, which failed with an integer
  parse error. It now links to Today and says "Selection queued" while a pass waits. Browser checks: scouts home,
  team and home focus (5 passed).
- Backup `/root/compose/watchcraft-backups/20261103-pre-shelf-41ded70`; rollback image
  `watchcraft:rollback-20261103-shelf-41ded70` retains `8869358`. Isolated restore ok (integrity, counts,
  profile/PIN checksum, started twice, routes 200, no tracebacks).
- `DEPLOYED_COMMIT=41ded70`; image `sha256:ae5f27716aadfa3a9185782fbea117f320657940b10d3f918753d560ac95a89e`, zero
  restarts; health, routes, integrity, profiles/PINs and configuration unchanged; bundle carries the new label.

## Deployment record — worker model on its own server, per-model test buttons (`8869358`, 2026-11-03)

- A worker with its own server and no model now uses a model that server lists instead of the judge's model name
  (which a separate vLLM rejects with 404); lookups are cached for 10 minutes and failures retried after 30 s. The
  model test shows the server's own error. In Settings the judge's model list and test now follow the judge fields
  and say "judge"; worker and embeddings keep their own tests.
- Release checks: 776 backend tests passed, six skipped; independent review approved; frontend type check clean
  (three existing CSS warnings), build ok; 12 browser checks (LLM providers, welcome, memory, hub).
- Backup `/root/compose/watchcraft-backups/20261103-pre-ai-8869358`; rollback image
  `watchcraft:rollback-20261103-ai-8869358` retains `3d00fb2`. Isolated restore: integrity, counts and profile/PIN
  checksum identical, started twice, routes 200, no tracebacks.
- `DEPLOYED_COMMIT=8869358`; image `sha256:8e2a7d00f09414e4c27b524dd9007fca443ec46d11a70a54069b6e2d130065a2`, zero
  restarts. Post-deploy: health, routes, integrity, profiles/PINs and configuration unchanged; the live worker
  resolves to `currentmodel` on `10.0.10.23`; the bundle carries "Test judge".

## Deployment record — fresh Hacker News, clean meme captions, faster settings (`3d00fb2`, 2026-11-03)

- General news queries ("latest news") read the Hacker News front page instead of a word search that only found
  days-old stories, so News and Today get current stories again. Links leave Mastodon meme captions; a bounded,
  versioned sweep pass (`watchcraft_meme_title_pass_v1`) cleans stored titles and their search index. Front-page web
  results need a section-like address. `get_settings` loads only known keys (the table holds thousands of cached
  video rows): `/api/hub/today` went from about 1.5 s to 0.1 s on a production copy.
- Release checks: 774 backend tests passed, six skipped; independent review approved after three rounds. Local
  Paper/Dusk checks on a sanitized production copy at 375 px phone and 1920×1080 TV mode: no horizontal overflow,
  Newsreader headings, TV arrow focus moves from the lead to Stories, Workshop opens on a Workshop page.
- Backup `/root/compose/watchcraft-backups/20261103-pre-feeds-3d00fb2`; rollback image
  `watchcraft:rollback-20261103-feeds-3d00fb2` retains `f3bbfca`. Isolated restore: integrity, counts and profile/PIN
  checksum identical, started twice, routes 200, no tracebacks; QA container removed.
- `DEPLOYED_COMMIT=3d00fb2`; image `sha256:e1dceb154bb73a3bc0da856673c2ee594415294e39e09055af639714910d17ad`, zero
  restarts. Post-deploy: local and public health 200, routes 200, integrity ok, profiles/PINs and `.env`/
  `compose.host.yaml` unchanged; live Hacker News front page returned 30 stories, `get_settings` 0.03 s.

## Deployment record — master curator and calm hub (`f3bbfca`, 2026-11-03)

- Ships `9181a57..f3bbfca`: master curator (rules, meme dedup, stored stories, scoring, tiered quotas, worker-model
  judging that yields to video, debounced post-run and nightly `hub_master` passes), daily Today edition, set-aside
  review and overrule, learned hub preferences, curated News/Meme Radar, editorial reading and Workshop pages,
  Paper/Dusk themes. Video routes unchanged. Calibrated on an offline replay of a local production snapshot
  (`scripts/master_curator_replay.py`).
- Release checks: 769 backend tests passed, six skipped; replay script tests 4 passed; frontend type check clean
  (three existing CSS warnings) and build ok; 38 browser checks across appearance, home focus, hub, memes, meme
  feedback, navigation, news, Scouts home, sidebar, Today and TV.
- Production runs one uvicorn worker and the hub scheduler only in `wc-api`, so the per-process `master.LOCK` covers
  every pass.
- Backup: `/root/compose/watchcraft-backups/20261103-pre-master-f3bbfca` (online and stopped-service databases,
  configuration, prior source); rollback image `watchcraft:rollback-20261103-master-f3bbfca` retains `98a0ba1`.
- Isolated restore ran `watchcraft:f3bbfca` on `127.0.0.1:6697`: integrity `ok`, every table count and the
  profile/PIN checksum identical, started twice, new tables created, `/`, `/today`, `/today/set-aside`, `/news`,
  `/memes`, `/discover`, `/settings`, `/tv`, `/shorts` 200, no tracebacks; QA container and data removed.
- Rollout extracted the committed source without deleting files, kept `.env` and `compose.host.yaml` (checksums
  verified), `DEPLOYED_COMMIT=f3bbfca`; API, worker and downloader run image
  `sha256:4f99ee15415d392d5bfd7cf780490cb97b9ba2dce4dfe34e01844f3f6dddaad8`, zero restarts.
- Post-deploy: local and both public health endpoints 200, all routes above plus `/team` 200, database integrity
  `ok`, profiles and PIN checksum unchanged. A first light pass ran for hub profiles 2 and 3: profile 2 got 377 verdicts (6 featured, 30 model-judged), both profiles have a 2026-11-03 edition, no tracebacks.
- Rollback: retag `watchcraft:rollback-20261103-master-f3bbfca` as `watchcraft:latest`, restore `source.tar.gz` from
  the backup, run the three-file Compose `up -d --no-build --force-recreate --no-deps --wait bt-api bt-worker
  bt-downloader`, restore `DEPLOYED_COMMIT`. New tables are additive; the old image ignores them.

## Deployment record — Shorts description hidden until tapped (`98a0ba1`, 2026-11-03)

- The Shorts player drew the description block (feed name, title, channel, score, summary) over every clip. It now
  starts hidden and toggles on a tap, click, or Enter/Space, and resets to hidden when the feed advances.
  `visibility: hidden` also stops the block from swallowing taps meant for the video.
- Release checks: 692 backend tests passed, six skipped; frontend type check clean (three existing CSS warnings) and
  build ok; 12 Shorts browser checks passed, including a new spec for hidden → shown → hidden → hidden on the next
  clip, plus the Shorts feed settings, feed guard, TV and phone suites.
- Backup: `/root/compose/watchcraft-backups/20261102-pre-shorts-meta-98a0ba1`, online and stopped-service databases
  plus prior source/configuration; rollback image `watchcraft:rollback-20261102-shorts-meta-98a0ba1` retains `7af36bf`.
- Isolated restore ran `watchcraft:98a0ba1` on `127.0.0.1:6697`: integrity `ok`, every original table count and the
  profile/PIN checksum identical, started twice, `/`, `/shorts`, `/discover` and `/settings` 200, no tracebacks.
- Rollout used rsync without `--delete` and preserved `.env` and `compose.host.yaml` (checksums verified before and
  after). `DEPLOYED_COMMIT=98a0ba1`; API, worker and downloader run image
  `sha256:913005e4289eccf81ae6e078902e0268ce4596a93ca2bd6a7c20985019d85642`, zero restarts.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/discover`, `/settings`, `/memes` and `/tv` return 200; database integrity `ok`, profiles and PIN
  checksum unchanged, configuration preserved; the deployed bundle contains the new hint string and the `.meta.show`
  rule; saved media returned 206 and decoded two seconds.
- Rollback: retag the retained rollback image as `watchcraft:latest` and run the existing three-file Compose
  `up -d --no-build --force-recreate --no-deps --wait bt-api bt-worker bt-downloader`; restore the prior marker.

## Deployment record — durable Seen it saves (`7af36bf`, 2026-11-02)

- Seen it now atomically upserts watched history and dismisses only this profile's candidates in one commit.
  Media eviction runs after the response in a separate session; failures are logged and the existing downloader
  sweep retries cleanup. An unavailable media mount no longer blocks the saved-state response.
- The client retries transient network/502/503/504 failures twice for this idempotent action, preserving the original
  profile (including the default-profile case) and avoiding duplicate gateway-error toasts.
- Release checks: 692 backend tests passed, six skipped; nine Seen it/feedback/remote browser checks passed, including
  injected 502 recovery and a profile selection change during retry. Frontend build/type check passed with three
  existing CSS warnings. Independent review approved the profile-preserving retry and backend changes.
- Backup: `/root/compose/watchcraft-backups/20261102-pre-seen-7af36bf`, online and stopped-service databases plus
  prior source/configuration; rollback image `watchcraft:rollback-20261102-seen-7af36bf` retains `36288b6`.
- Isolated restored copy initialized twice with every original table count and profile/PIN checksum preserved.
  Two real HTTP Seen it requests against disposable records returned 200 in 104 ms and 4 ms; one finished history
  row and the dismissed candidate were verified. No production viewing history was altered for this check.
- Production API/worker/downloader run image `sha256:1f7e62a90b3fd8cc8c408ac11e815d8d7afc792368755cd77b6c713371bda226`,
  marker `7af36bf`, zero restarts. Both public health endpoints and affected routes returned 200; integrity and
  profile/PIN/configuration checks passed. Saved media returned 206 and decoded two seconds without errors.
- Rollback: retag the retained rollback image as `watchcraft:latest` and run the existing three-file Compose
  `up -d --no-build --force-recreate --no-deps --wait bt-api bt-worker bt-downloader`; restore the prior marker.


## Deployment record — relevant person research (`36288b6`, 2026-11-02)

- Person research requires full-name or supplied-alias evidence, splits accidentally combined numeric handles,
  prioritizes quoted identity searches, and excludes zero-scored discoveries. Old unrelated findings are filtered
  before report/display limits; mixed-identity old reports require regeneration without renumbering their citations.
- Reports request synthesized, cited paragraphs with captured source text when available and numbered original-source
  links. Model failure is reported explicitly rather than replacing the summary with a raw search-result list.
- Release checks: 691 backend tests passed, six skipped; five Hub browser checks; frontend build and type check passed
  with three existing CSS warnings. Independent diff review approved the identity filtering and alias handling.
- Backup: `/root/compose/watchcraft-backups/20261102-pre-research-36288b6`, including online and final stopped-service
  SQLite backups, prior source/configuration and rollback image `watchcraft:rollback-20261102-research-36288b6`.
  Isolated restore initialized twice with every table count and profile/PIN checksum preserved; startup/health and
  identity checks passed. Production overlays, including the NVIDIA override, were retained unchanged.
- API, worker and downloader ran image `sha256:26082abc9090a0504c2bd5d442d251a33218a6fc53abcc067fafcfd2c558600c`,
  revision marker `36288b6`, with zero restarts. Local/public health, research/Find/TV/Memes routes, database integrity,
  profile/PIN checksum and configuration preservation passed. Saved media returned HTTP 206 and decoded two seconds.
  No real profile PINs were changed. The latest named report regeneration returned an explicit summary-unavailable state.


## Deployment record — search covers the whole video library (`4c99b05`, 2026-11-02)

- Covers `f66b85f..4c99b05`. Searching "octopus" returned nothing while the household held thirteen octopus
  videos: the video half of `/hub/search` only looked at candidates of this profile's playlists and its watch
  history, so a video another playlist collected was invisible. The page promises "everything WatchCraft has
  collected", and the video library is household media, so the search now covers all of it — channels this profile
  blocked stay out, and the page is adult-only anyway because the hub requires an adult profile. The video cap went
  from 12 to 24 and results are ordered by publish date.
- The empty state no longer pretends nothing exists: it names the scope ("this profile's findings and the
  household's video library") and offers the live YouTube search, and a typed query in the command palette offers
  "Search YouTube for …" as its first entry.
- Local verification: **688 backend tests passed, six skipped** (the video-search test now asserts that another
  playlist's video is found and a blocked channel is not); **2 new browser specs** for the empty state and the
  palette entry; **21 navigation/a11y/hub browser tests**; svelte-check 0 errors (three existing CSS warnings);
  production build.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-search-library`
  (integrity `ok`; 6 profiles, 7,766 videos, 29 categories, 501 items, 7,395 candidates, 2,299 downloads, 18
  sources, 9 curators). Rollback image `watchcraft:rollback-20261102-search-library` retains `f66b85f`
  (`f88da9de82d5`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:4c99b05` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, all routes 200, no tracebacks. A disposable adult QA
  profile reproduced the complaint and the fix: `/api/hub/search?q=octopus` returned **13 videos** (previously 0)
  and `q=linux` returned 24 videos plus 14 items. The disposable containers and data were removed.
- Rollout used rsync without `--delete` and preserved `.env` and `compose.host.yaml` (checksums verified before and
  after). `DEPLOYED_COMMIT=4c99b05`; API, worker and downloader run image
  `sha256:98fc935a2e4f2ca3074748ce8b0a79790f86ed39940e9e6fd9dc40d640e04bbf` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/find`, `/memes`, `/api/categories` and `/api/downloads` return
  200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum `2c415ddd39a318be`); the
  deployed bundle contains the new empty state and the backend the household-wide video query.
- Rollback: `docker tag watchcraft:rollback-20261102-search-library watchcraft:latest` and rerun the three-file
  compose command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027. Item search stays per profile (findings carry per-profile state); only the video library
  is household-wide.

## Deployment record — meme language detection, language settings, GitHub backoff (`f66b85f`, 2026-11-02)

- Covers `3ec97d0..f66b85f`. Three changes.
- `19a3b5b`: Mastodon often leaves a status language null, so 27 of the collected memes had no language and could
  not offer the language reason. `memes.detect_language` now fills the gap from the caption: only words that belong
  to a single language count, and only a clear winner is used, so an emoji-only or two-word caption stays unknown
  rather than guessing and hiding the wrong memes. Existing examples are backfilled on the next sweep. Settings
  gains a **Meme languages** tab listing every language in the feed with its count and a Shown/Hidden switch, and
  `/memes/facets` reports the hidden list.
- `f66b85f`: the 6.5 s GitHub gap sat at 92% of the ten-a-minute search budget and six curators share one source, so
  a cycle asks for about thirty searches; the window tipped over and every later query 403'd, which is why the
  source still reported a rate limit after the first fix. The gap is now 12 s (half the budget), a refusal that
  names a reset blocks the host until that reset, and the engine stops asking that source instead of recording the
  same error six times.
- Local verification: **688 backend tests passed, six skipped** (new: language detection, the facets hidden list,
  the block-until-reset behaviour and the engine stopping after the first rate-limited query); **6 meme-feedback
  browser specs** including the Settings language switch; **32 settings-walk/a11y/memes browser tests**;
  svelte-check 0 errors (three existing CSS warnings); production build.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-lang-settings`
  (integrity `ok`; 6 profiles, 7,746 videos, 29 categories, 446 items, 7,340 candidates, 2,287 downloads, 18
  sources, 9 curators). Rollback image `watchcraft:rollback-20261102-lang-settings` retains `3ec97d0`
  (`85ba954bf77e`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:f66b85f` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, all routes 200, no tracebacks. On that copy the backfill
  filled 12 of the 27 unknown languages (`en` 49→58, `es` 2→3, `pt` 0→2, 15 left unknown), and a disposable adult
  QA profile read `/memes/facets` (languages with counts, `hidden_languages: []`), hid `de` and saw it reported,
  then undid it. The disposable containers and data were removed.
- Rollout used rsync without `--delete` and preserved `.env` and `compose.host.yaml` (checksums verified before and
  after). `DEPLOYED_COMMIT=f66b85f`; API, worker and downloader run image
  `sha256:f88da9de82d501f5e26f7901b296918ab269e7740f72b017c06ee9b2a59a77ce` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/news`, `/library`, `/memes`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum
  `2c415ddd39a318be`); the deployed bundle contains the Meme languages tab and the backend exposes
  `detect_language` and `RateLimited`.
- Rollback: `docker tag watchcraft:rollback-20261102-lang-settings watchcraft:latest` and rerun the three-file
  compose command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027. The language detector is a stopword heuristic for Latin-script captions; Cyrillic, Greek
  and CJK captions stay unknown, and 15 memes remain unknown because their captions carry no words at all.

## Deployment record — thumbs down asks why, and a language answer really hides it (`3ec97d0`, 2026-11-02)

- Covers `1fb720f..3ec97d0`. Learning the title words was wrong: a German meme taught the curator "humor", "memes"
  and "source", which would suppress good memes too. A thumbs down now always asks why, and only the answer is
  learned.
- The question offers the reasons that actually work as one click: the meme's language (when it is not English),
  its community and its format, plus free text. A language answer hides that language from the meme feed for the
  profile, stored in `Profile.prefs['hidden_meme_languages']` through `POST /memes/languages/{code}/hide`. That is
  the only way "I do not speak German" can mean anything: the ranking only matches words in the title, so learning
  "german" would never fire. Any other answer goes to the curator's `tuned_down` as before. Skipping still leaves
  the meme hidden and keeps an Undo within reach.
- Local verification: **684 backend tests passed, six skipped** (two new: hiding a language removes it from the
  feed and undo restores it; nonsense codes are rejected); **5 meme-feedback browser specs** including the German
  case where the language chip posts `/memes/languages/de/hide`; **9 browser tests** across memes and a11y;
  svelte-check 0 errors (three existing CSS warnings); production build.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-why-ask`
  (integrity `ok`; 6 profiles, 7,731 videos, 29 categories, 411 items, 7,325 candidates, 2,285 downloads, 18
  sources, 9 curators). Rollback image `watchcraft:rollback-20261102-why-ask` retains `1fb720f`
  (`37e229eb4d6c`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:3ec97d0` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, all routes 200, no tracebacks. A disposable adult QA
  profile drove the real path: its feed held 18 items with one Spanish meme, `POST /memes/languages/es/hide`
  returned `hidden_languages: ['es']`, the feed dropped to 17 with no Spanish left, and `{hidden: false}` restored
  18 with the Spanish meme back. The disposable containers and data were removed.
- Rollout used rsync without `--delete` and preserved `.env` and `compose.host.yaml` (checksums verified before and
  after). `DEPLOYED_COMMIT=3ec97d0`; API, worker and downloader run image
  `sha256:85ba954bf77e49cd6626ffae2c7568a3fceab33d2362a13fca9dc24bb2944e02` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/news`, `/library`, `/memes`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum
  `2c415ddd39a318be`); the deployed bundle contains the "Why hide this?" dialog and the backend exposes
  `hidden_meme_languages`.
- Rollback: `docker tag watchcraft:rollback-20261102-why-ask watchcraft:latest` and rerun the three-file compose
  command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027. Hidden languages live in profile prefs and have no settings screen yet; the only way to
  undo one is the toast's Undo.

## Deployment record — thumbs down always hides, and asks why when it cannot learn (`1fb720f`, 2026-11-02)

- Covers `0eaf673..1fb720f`. A meme whose title had no distinctive words made the curator feedback answer 422
  "Nothing distinctive in this finding to learn from", and because that call ran before the dismissal the whole
  thumbs down aborted: the meme stayed in the feed and the person got an error instead of a result. The Meme
  Tracker's own queries (`memes`, `linux memes`, `programming humor`, `AI memes`) are excluded from the words it
  learns, so a meme titled only "#memes" has nothing left to learn from — the case that failed.
- The dismissal now happens first, so the meme always leaves the feed. The feedback runs after it; when it still
  has nothing to learn from, the tracker asks "What should I avoid?" in a small dialog and sends that word as the
  reason. The feedback call no longer raises its own toast, so the question replaces the error instead of
  appearing beside it. `POST /items/{iid}/curator-feedback` accepts optional `terms` for exactly this case: the
  person's word is cleaned, bounded to three and learned into the curator's `tuned_down` instead of the title words.
- Local verification: **682 backend tests passed, six skipped** (two new cover the typed reason and its bounds);
  **4 meme-feedback browser specs** including one where the feedback answers 422 and the meme still disappears,
  the dialog appears and the typed word is sent as `terms`; **15 browser tests** across memes and a11y;
  svelte-check 0 errors (three existing CSS warnings); production build.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-thumbs-reason`
  (integrity `ok`; 6 profiles, 7,719 videos, 29 categories, 401 items, 7,294 candidates, 2,283 downloads, 18
  sources, 9 curators). Rollback image `watchcraft:rollback-20261102-thumbs-reason` retains `0eaf673`
  (`c8e20dfe5169`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:1fb720f` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, all routes 200, no tracebacks. A disposable adult QA
  profile drove the real path: the meme feed held 18 items, a thumbs down dismissed the item (200, `dismissed:
  true`), the feed dropped to 17 with the item absent, and a feedback call carrying `terms: ["  Spongebob "]`
  returned `added: ['spongebob']` with `tuned_down` growing by exactly that word. The disposable containers and
  data were removed.
- Rollout used rsync without `--delete` and preserved `.env` and `compose.host.yaml` (checksums verified before and
  after). `DEPLOYED_COMMIT=1fb720f`; API, worker and downloader run image
  `sha256:37e229eb4d6c0cb534deac3168b3aea3e744108e3ae5653c2cfe7b5da4638c6b` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/news`, `/library`, `/memes`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum
  `2c415ddd39a318be`); the deployed bundle contains the "What should I avoid?" dialog and the backend exposes
  `_clean_terms`.
- Rollback: `docker tag watchcraft:rollback-20261102-thumbs-reason watchcraft:latest` and rerun the three-file
  compose command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027. A thumbs down hides the item and ranks its shape lower; it does not hard-exclude the
  words, so a different meme with the same words can still arrive.

## Deployment record — GitHub rate limits and meme thumbs-down hiding (`0eaf673`, 2026-11-02)

- Covers `dd88919..0eaf673`. Two changes.
- `b40dba5` (B-035): the GitHub repositories source failed with a bare "Source returned HTTP 403". GitHub's
  anonymous search API allows ten requests a minute and a curator with several queries exhausted it; the source's
  own check history showed `ok` at 13:43:30 and 13:44:14, then `failed` at 13:44:17, 13:44:18 and 13:47:47. Search
  calls are now spaced 6.5 s apart in-process, so eight queries stay inside the minute and concurrent curators
  queue instead of colliding. A refusal that names a reset reads "GitHub rate limit reached: 10 searches a minute
  without a token. It resets in 42s."; a 403 without a reset keeps its plain HTTP error. `curation.fetch` now reads
  `x-ratelimit-reset` when a host reports an exhausted quota, so `FetchError` carries the wait without `Retry-After`.
- `0eaf673`: a meme thumbs down only nudged the curator's ranking, so the meme stayed in the feed. It now also
  dismisses the item: the card leaves the feed immediately, the drawer closes, and the item stays dismissed for
  that profile, which keeps it out of Discover and Meme Radar for good. The curator feedback still runs so the
  shape ranks lower in future. The toast carries an Undo for eight seconds because a dismissal is not visible
  anywhere else in the UI.
- Local verification: **680 backend tests passed, six skipped** (five new cover the pacing, both GitHub providers'
  messages and the "not a quota" case); **7 browser tests** across meme-feedback and memes, including that a thumbs
  down removes the card, posts `{dismissed: true}` and offers Undo, and that the drawer closes; svelte-check 0
  errors (three existing CSS warnings); production build.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-meme-hide`
  (integrity `ok`; 6 profiles, 7,706 videos, 29 categories, 401 items, 7,281 candidates, 2,282 downloads, 18
  sources, 9 curators). Rollback image `watchcraft:rollback-20261102-meme-hide` retains `dd88919`
  (`3dcbf25c4c4a`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:0eaf673` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, all routes 200, no tracebacks. On that copy the GitHub
  fix was exercised against the real API from inside the image (first search 0.7 s, second 6.6 s, both 15 items),
  and a disposable adult QA profile drove the dismissal path: the meme feed went from 18 items to 17 with the
  dismissed item absent, and `{dismissed: false}` brought it back to 18. The disposable containers and data were
  removed.
- Rollout used rsync without `--delete` and preserved `.env` and `compose.host.yaml` (checksums verified before and
  after). `DEPLOYED_COMMIT=0eaf673`; API, worker and downloader run image
  `sha256:c8e20dfe51690ccee523c021fcfd54948b97e1ed154c2b0e00d63fe924fe6951` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/news`, `/library`, `/memes`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum
  `2c415ddd39a318be`); the deployed bundle contains the new hide strings and the backend exposes the GitHub pacer.
- Rollback: `docker tag watchcraft:rollback-20261102-meme-hide watchcraft:latest` and rerun the three-file compose
  command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027. A thumbs down hides the item and ranks its shape lower; it does not hard-exclude the
  words, so a different meme with the same words can still arrive.

## Deployment record — meme thumbs up/down (`dd88919`, 2026-11-02)

- Covers `903b90e..dd88919`. Meme Radar had no way to say which memes were wanted short of opening an item and
  using the curator panel. Each meme card now carries thumbs up and down, and the drawer repeats them as labelled
  buttons. A thumb sends the existing `curator-feedback` verdict to every curator that found the meme, so the
  learned words land in that curator's `tuned_up`/`tuned_down` and rank future finds; the toast names what was
  learned. Items nobody curated show no thumbs.
- The card body stopped being one big button (a button cannot contain buttons): the picture and caption are the
  button that opens the drawer and the actions sit beside it, so a thumb never opens the meme. Item payloads now
  carry `curator_ids` beside `curator_names`, which is what lets a card send feedback without a second request.
- Local verification: **675 backend tests passed, six skipped** (the item-payload assertion now covers
  `curator_ids`); **16 browser tests** across a11y/navigation/hub/memes plus **two new meme-feedback specs** that
  stub the API and assert the POST body, the pressed state, that a thumb does not open the drawer and that an
  uncurated meme offers no thumbs; svelte-check 0 errors (three existing CSS warnings); production build; card
  layout, thumb containment and click targets checked at desktop and 390 px.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-meme-thumbs`
  (integrity `ok`; 6 profiles, 7,701 videos, 29 categories, 401 items, 7,276 candidates, 2,277 downloads, 18
  sources, 8 curators). Rollback image `watchcraft:rollback-20261102-meme-thumbs` retains `903b90e`
  (`5871cb7fdb59`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:dd88919` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, `/`, `/shorts`, `/agent`, `/settings`, `/discover`,
  `/news`, `/library`, `/memes` and `/api/categories` all 200, no tracebacks. On that copy a disposable adult QA
  profile (created, PIN-set and given the meme curator inside the copy only) exercised the real path: the meme
  feed returned `curator_ids: [4]` with `curator_names: ['Meme Tracker']`, and a thumbs-up POST on item 151
  returned 200 with `added: ['disappointed', 'grindr', 'source']`, after which the curator's `tuned_up` held
  exactly those words and `tuned_down` stayed empty. The disposable containers and data were removed.
- Rollout used rsync without `--delete` (four frontend files changed, one added, one backend file changed) and
  preserved `.env` and `compose.host.yaml` (checksums verified before and after). `DEPLOYED_COMMIT=dd88919`; API,
  worker and downloader run image
  `sha256:3dcbf25c4c4a70c4fe6e944c834759ac002052bbff69837a14e7583128a9b42c` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/news`, `/library`, `/memes`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum
  `2c415ddd39a318be`); the deployed bundle contains the thumb labels and the backend exposes `curator_ids`; a
  read-only serialization of live meme items returns `curator_ids: [4]` for the Meme Tracker. The curation timing
  log keeps growing (345 events) and the worker is researching normally.
- Rollback: `docker tag watchcraft:rollback-20261102-meme-thumbs watchcraft:latest` and rerun the three-file
  compose command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027.

## Deployment record — live item filters and tag search (`903b90e`, 2026-11-02)

- Covers `cce22ea..903b90e`. Discover, News, Library and Meme Radar only filtered after a submit, and News and
  Library had no text filter at all. A shared `LiveFilter` writes the existing `q` URL parameter as the user types
  (250 ms debounce, `replaceState`, focus kept): Discover, Library and Meme Radar re-query, News filters its
  already-loaded stories in place.
- Item search now also matches tags. Source tags in `meta.tags` and the profile's own tags in `itemstate.tags` are
  matched through `json_each`, so a tag surfaces an article whose text never mentions it; `-term` excludes tagged
  items too and quoted phrases stay text-only. Cards show source tags beside the profile's own tags and both are
  buttons that set the filter; pages without a filter keep rendering them as plain text.
- Local verification: **675 backend tests passed, six skipped** (four new cover source tags, own tags, negation and
  the term parser); **25 browser tests** across hub/memes/navigation/a11y plus **four new live-filter specs** that
  stub the API and assert the list narrows without a submit and that a tag chip filters; svelte-check 0 errors
  (three existing CSS warnings); production build; rendered desktop and 390 px layouts checked for chip containment
  and horizontal overflow.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-live-filter`
  (integrity `ok`; 6 profiles, 7,701 videos, 29 categories, 300 items, 7,276 candidates, 2,274 downloads, 17
  sources, 5 curators). Rollback image `watchcraft:rollback-20261102-live-filter` retains `cce22ea`
  (`9d1be9f1544a`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:903b90e` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical, started twice, `/`, `/shorts`, `/agent`, `/settings`, `/discover`,
  `/news`, `/library`, `/memes` and `/api/categories` all 200, no tracebacks. On that copy a disposable adult QA
  profile (created and PIN-set inside the copy only) exercised the HTTP path: `q=memes` returned 21 items,
  `q=spongebob` 4, `q=linux` 7, `q=selfhosted` 0, at about 10 ms per query. Direct query comparison on the same
  data: `memes` 64 text-only → 80 tag-aware (+16), `spongebob` 17 → 17, `linux` 28 → 28. The disposable
  containers and data were removed.
- Rollout used rsync without `--delete` (six frontend files changed, three added, one backend file changed) and
  preserved `.env` and `compose.host.yaml` (checksums verified before and after). `DEPLOYED_COMMIT=903b90e`; API,
  worker and downloader run image
  `sha256:5871cb7fdb59220814d209a72f7c2726ee2318ffa3478240aa5bde4511528f26` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org` plus `https://watchcraft.tdgs.org` return 200; `/`,
  `/shorts`, `/agent`, `/settings`, `/discover`, `/news`, `/library`, `/memes`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum
  `2c415ddd39a318be`); the deployed bundle contains the new filter strings and the backend exposes `tag_terms`; a
  read-only query against the live database reproduces the isolated result (`memes` 64 → 80). The curation timing
  log from the previous release keeps growing (235 events) and the worker is judging normally.
- Rollback: `docker tag watchcraft:rollback-20261102-live-filter watchcraft:latest` and rerun the three-file
  compose command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (curation measurement window), B-033, B-034, T-075 Stages E–G, and the physical device checks
  V-002/V-003/V-010/V-027. The tag match is a `json_each` scan: fine at 300 items, the first thing to index if the
  table grows.

## Deployment record — curator-form defaults and video-curation throughput (`cce22ea`, 2026-11-02)

- Covers `a5f0e10..cce22ea`: `b3f280e` (default Mastodon and Lemmy endpoints in the curator form) and
  `068b92b` + `cce22ea` (T-075 Stages A–C and the Stage D quantum).
- `b3f280e`: the Where to look section offers one-click defaults — six Lemmy communities, five Mastodon trend
  feeds and six Mastodon instances — instead of only a typed URL. Clicking an existing default toggles its
  selection rather than duplicating the source. Every default was probed live before shipping; `lemmy.ca` and
  `discuss.tchncs.de` were dropped (404/400) and `infosec.exchange`, `mstdn.social` and `mstdn.ca` were dropped
  because they reject anonymous public timelines.
- `068b92b`: closed-vocabulary timing spans written to a private rotating `curation-timing.log` in the data
  directory, with `scripts/curation_timing_report.py` and `scripts/curation_lifecycle_report.py`; cache-only
  metadata lookup that cannot extract on a miss; caption-track check before the transcript turn; cached
  research that advances during a YouTube blocker; conditional candidate writes that never overwrite a manual
  decision or dismissal; and an AI lane that yields after one verdict once research has finished.
- `cce22ea`: curation acquires `min(setting, JUDGE_COMMENTS_MAX)` comments. Production asked for 60 while the
  judge reads 30. The player keeps its own on-demand fetch; its warm start from a curated video is now 30
  comments, which is the partial-cache behaviour already tracked as B-034.
- Local verification: **671 backend tests passed, six skipped**; **13 script tests passed**; svelte-check 0
  errors (three existing CSS warnings); production build; five Hub browser tests including the new default-endpoint
  spec. Offline before/after: warm research 3 limiter turns/3 calls → 0/0, cached-metadata + no-caption 3/3 → 1/1,
  comments 60 → 30, and a synthetic 20-item AI backlog first yield 400 s → 80 s with identical verdict throughput.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-curation-throughput`
  (integrity `ok`; 6 profiles, 7,674 videos, 29 categories, 147 history, 279 items, 279 capture jobs, 2,268
  downloads, 9 sources, 5 curators). Rollback image `watchcraft:rollback-20261102-curation-throughput` retains
  `a5f0e10` (`d8b33137875a`). Saved media was unchanged.
- Isolated restore of the online backup ran `watchcraft:cce22ea` on `127.0.0.1:6697`: integrity `ok`, every count
  and the profile/PIN checksum identical to production, started twice, `/`, `/shorts`, `/agent`, `/settings`,
  `/api/categories` and `/api/downloads` all 200, no tracebacks. A 100-second worker run on that copy produced
  `curation-timing.log` (0600) with 24 events across 12 stages, logged `Read 30 comments`, and the report script
  summarised it (comments p50 3.3 s, metadata 4.4 s, limiter_wait p50 13.7 s, metadata_cache misses under 8 ms).
  The disposable containers and data were removed.
- Rollout used rsync without `--delete` (five backend files changed, four backend files added, two frontend files
  changed, five scripts added) and preserved `.env` and `compose.host.yaml` (checksums verified before and after).
  `DEPLOYED_COMMIT=cce22ea`; API, worker and downloader run image
  `sha256:9d1be9f1544a65616d242d3990202a15a48836c51a90a03990cc614481e59c8c` with zero restarts and no tracebacks.
- Post-deploy: local `/api/health` OK and `https://wc.tdgs.org/api/health` plus `https://watchcraft.tdgs.org/api/health`
  return 200 (`bt.tdgs.org` returns 502 because that hostname belongs to the retired BetterTube install, not to
  WatchCraft); `/`, `/shorts`, `/agent`, `/settings`, `/discover`, `/api/categories` and `/api/downloads` return 200;
  DB integrity `ok`; profiles, roles and both PIN records unchanged (checksum `2c415ddd39a318be`); the deployed
  bundle contains the new default-endpoint strings; `data/curation-timing.log` is live and the worker logged
  `Read 30 comments`, `caption_check no_track` and `metadata_cache miss` within the first minutes.
- Rollback: `docker tag watchcraft:rollback-20261102-curation-throughput watchcraft:latest` and rerun the three-file
  compose command with `up -d --no-build --force-recreate --wait` (no schema change).
- Still open: V-040 (compare a fixed post-deploy window against the pre-deploy baseline), B-033, B-034, T-075
  Stages E–G, and the physical device checks V-002/V-003/V-010/V-027.

## Deployment record — child-friendliness scoped to the Kid-safe toggle (`6e82ee9`, 2026-11-02)

- Covers `4789e51..6e82ee9`. B-032: the judge prompt carried the child-safety rules for every playlist. The
  shared `FAMILY` preamble described the household as "parents and young kids", and the red-flag list
  unconditionally included sexual content, gore, swearing, scary content and "dangerous stunts kids could
  copy", so a playlist without the toggle was judged against what suits a six-year-old. Production has 27
  playlists, 14 of them without the toggle (Grownups, Cooking, Linux news, CVEs, Tech humor, …).
- The child-safety block now lives in `KID_SAFE_JUDGE` and is appended by `judge_system(cat)` only when that
  playlist has the toggle on. The base prompt keeps the integrity red flags (clickbait, low-effort,
  engagement bait, sponsorship, gambling, misinformation, cruelty presented as entertainment) and states
  that adult language, mature themes, violence, horror and sexual content are not red flags by themselves.
  `kid_safe` is still reported but is information only unless the playlist is marked kid-safe. `FAMILY` no
  longer assumes young children. The brief autofill no longer assumes a young audience or lists pranks and
  shouting as things to avoid. The per-playlist gate in `judge.decide` was already conditional and is
  unchanged.
- Local verification: **600 backend tests passed, two skipped**. A new test asserts the child-safety block is
  absent without the toggle and present with it; the brief-autofill test asserts the system prompt no longer
  assumes a young audience.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-kid-safe-scope`
  (integrity `ok`; 6 profiles, 7,496 videos, 27 categories, 141 history, 274 items, 274 capture jobs).
  Rollback image `watchcraft:rollback-20261102-kid-safe-scope` retains `4789e51`.
- Isolated restore of the online backup ran `watchcraft:6e82ee9`. The same synthetic stand-up clip (swearing,
  adult grievances) was judged twice against the real model at `10.0.10.23:8080`: under a playlist **without**
  the toggle it scored 62 with the single red flag "surface-level travel humor with minimal development" and
  no mention of swearing or children; under a playlist **with** the toggle it scored 12 with "swearing:
  'damn', 'what the hell'", "adult-themed comedy not suitable for young children" and "not appropriate for a
  ~6-year-old viewer". The API then started twice on that copy: health OK, integrity `ok`, all counts
  preserved, no tracebacks. `autofill-brief` for "Stand-up comedy" returned an adult-appropriate brief naming
  Dave Chappelle and John Mulaney with no child framing. The disposable container and data were removed.
- Rollout used rsync without `--delete` (five files changed, nothing removed) and preserved `.env` and
  `compose.host.yaml` (checksums verified before and after). `DEPLOYED_COMMIT=6e82ee9`; API, worker and
  downloader run image `sha256:de20ebb2e6b1…` with zero restarts and no tracebacks.
- Post-deploy: local and public `/api/health` OK; `/`, `/shorts`, `/agent`, `/settings`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged; the running
  image reports the KID-SAFE block absent for a non-kid-safe category and present for a kid-safe one, and
  `FAMILY` no longer mentions young kids.
- Rollback: `docker tag watchcraft:rollback-20261102-kid-safe-scope watchcraft:latest` and rerun the compose
  command (no schema change).
- Still open: B-008 (merge pause), B-019 (browser egress), T-069 (non-YouTube providers), and the physical
  device checks V-002/V-003/V-010/V-027. The planner prompt still lists "prank" and "reaction" among the
  formats to avoid, which will fight a playlist explicitly about them.

## Deployment record — dead download queue rows (`4789e51`, 2026-11-02)

- Covers `598e242..4789e51`. B-031: `next_download` only returns a non-pinned row whose candidate is still
  accepted and not dismissed, so rows left behind by a deleted or dismissed candidate sat in `queued`
  forever. `drop_dead_queue` deletes queued/error rows with no accepted, non-dismissed candidate; it runs
  next to `purge_unwanted` at the top of every `iterate`, so the count self-corrects within one cycle even
  while the cache is disabled. Pinned rows are kept (they are returned before the candidate lookup, so a
  manual "Save offline" still runs) and genuine `error` rows with a live candidate are kept.
- Local verification: **599 backend tests passed, two skipped** (two new tests cover the keep/drop matrix and
  the reported count). Against a copy of the production `download`/`candidate` tables: 24 queued/error rows
  in, the six dead ones out, all 18 real error rows kept.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-dead-queue`
  (integrity `ok`; 6 profiles, 7,406 videos, 27 categories, 141 history, 267 items, 267 capture jobs).
  Rollback image `watchcraft:rollback-20261102-dead-queue` retains `598e242`.
- Isolated restore of the online backup ran `watchcraft:4789e51`. With the cache disabled so the copy could
  not download, the downloader logged `dropped 6 dead queue rows (no accepted candidate)` and the queue went
  from 6 to 0 while all 18 `error` rows survived; a pinned row with no candidate was kept. The API then
  started twice on that copy: health OK, integrity `ok`, all counts preserved, no tracebacks, and
  `/api/downloads` reported `queued: 1` (the pinned probe) instead of 6. The disposable container and data
  were removed.
- Rollout used rsync without `--delete` (three files changed, nothing removed) and preserved `.env` and
  `compose.host.yaml` (checksums verified before and after). `DEPLOYED_COMMIT=4789e51`; API, worker and
  downloader run image `sha256:d2ed5f8787bb…` with zero restarts and no tracebacks.
- Post-deploy: the downloader logged the same `dropped 6 dead queue rows` line, `download` has no `queued`
  rows left, `/api/downloads` reports `{'done': 1115, 'error': 18}` and `queue 0` / "Nothing to download".
  Local and public `/api/health` OK; `/`, `/shorts`, `/agent`, `/settings`, `/api/categories` and
  `/api/downloads` return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged; an
  uncommitted probe confirmed `next_download` still returns a pinned row with no candidate, so manual
  "Save offline" is unaffected.
- Rollback: `docker tag watchcraft:rollback-20261102-dead-queue watchcraft:latest` and rerun the compose
  command (no schema change; the six deleted rows were dead and are not restored).
- Still open: B-008 (merge pause), B-019 (browser egress), T-069 (non-YouTube providers), and the physical
  device checks V-002/V-003/V-010/V-027.

## Deployment record — judge calibration, date-aware discovery, profile-scoped manifests and brief autofill (`598e242`, 2026-11-02)

- Covers `9c27ded..598e242`. B-028: the judge no longer reads popularity as quality, treats a missing
  transcript/comment section/description as missing evidence rather than a fault (in the system prompt and at
  the point of use), only treats a listed rejection as precedent when its reason is a real quality or safety
  failure, reads broad briefs at their own breadth, and lets variety break close calls instead of tightening
  them. Safety rules and the kid-safe gate are unchanged.
- B-029: the planner receives today's date and recency guidance, future-dated queries and seeds are dropped
  from the rotation and the prompt, and `approximate_date` is enabled for search and channel tabs so
  `published_at` exists at discovery and `max_age_days` can act before enrichment.
- B-024: Shaka now sends `X-Profile-Id` on same-origin manifest requests only, the prefetch fetch sends it
  too, and playback reads profile-merged player prefs instead of the global `max_height`.
- T-071: `POST /api/categories/autofill-brief` writes a 2-4 sentence brief from the playlist name; the form
  button warns without a name, regenerates an AI-written brief in one click and asks for a second press
  before replacing typed text.
- Local verification: **597 backend tests passed, two skipped**; svelte-check 0 errors (three existing CSS
  warnings); production build; **19 focused browser tests passed** including the new brief-autofill spec.
  The full browser suite ran 138 passed with 11 two-minute timeouts in tv/welcome/voice-search/memory specs
  that all pass in isolation on this loaded machine.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-brief-autofill`
  (integrity `ok`; 6 profiles, 7,359 videos, 26 categories, 140 history, 267 items, 267 capture jobs).
  Rollback image `watchcraft:rollback-20261102-brief-autofill` retains `9c27ded`.
- Isolated restore of the online backup ran `watchcraft:598e242` on `127.0.0.1:6698`, started twice:
  integrity `ok`, all counts preserved, no tracebacks. On that copy, with profile 1 PIN-locked, the manifest
  endpoint returned **403 without the header** (the B-024 bug) and **200 with a 282 KB MPD for unlocked
  profile 4**, while PIN-protected profile 2 stayed 403. `POST /api/categories/autofill-brief` returned a
  usable brief from the real model. The disposable container and data were removed.
- Rollout used rsync without `--delete` (12 source files changed, nothing removed) and preserved `.env` and
  `compose.host.yaml` (checksums verified before and after). `DEPLOYED_COMMIT=598e242`; API, worker and
  downloader run image `sha256:844e7b9fc470…` with zero restarts and no tracebacks.
- Post-deploy: local and public `/api/health` OK; `/`, `/shorts`, `/agent`, `/settings` and `/api/categories`
  return 200; DB integrity `ok`; profiles, roles and both PIN records unchanged; saved media served HTTP 206
  and decoded 20 frames; the deployed bundle contains the new brief-autofill strings and the backend exposes
  both autofill routes; the running image reports every new judge/planner prompt line and `future_dated`.
  Live `autofill-brief` on prod returned a usable brief for "Space for kids".
- Rollback: `docker tag watchcraft:rollback-20261102-brief-autofill watchcraft:latest` and rerun the compose
  command (no schema change in this release, so no data restore is needed).
- Still open: B-008 (merge pause), B-019 (browser egress), T-069 (non-YouTube providers), and the physical
  device checks V-002/V-003/V-010/V-027.

## Deployment record — persistent channel availability feedback (`2ec62a9`, 2026-11-02)

- Discovery plans retain up to 32 temporary unavailable-channel records across replanning,
  process restarts and playlist-brief/context invalidation. Records use the actual requested
  public tab URL: equivalent handle spellings/URL variants share evidence, channel IDs retain
  their case, and missing Shorts tabs do not disable videos/streams. The 24-hour retry expires
  automatically. Invalid/malformed/expired history is pruned. Successful extraction clears
  only its exact reference/tab.
- Only definitive `YtUnavailable` results enter this history, including an RSS failure followed
  by an unavailable public-channel fallback. Transient errors and blocking403/429 retain their
  existing retries/backoff. Empty valid channels are not recorded as failures. Explicit family
  seed preferences are retained while unavailable sources are temporarily skipped.
- Planner prompts receive technical failure/retry evidence, separate from taste feedback.
  Deterministic source filtering suppresses repeated references even if the model ignores that
  evidence. Malformed generated channel URLs are rejected before discovery. Up to 12 observed
  exact-name channel records can supply a stable ID after a guessed handle fails; these are
  possible alternatives rather than silently inferred aliases or creator/topic bans.
- Independent review approved the final patch. Full backend suite: **589 passed, six skipped**;
  after the final observed-channel-alternative addition, **77 agent tests passed, one skipped**.
  Frontend check/build passed with the existing three CSS warnings; **four focused browser
  regressions passed** for activity layout and separate feed configuration.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-channel-retry`.
  Rollback image `watchcraft:rollback-20261102-channel-retry` retains `a3dea76`. Original rows
  matched after two isolated initializations; network-isolated server startup and affected
  routes passed. Private configuration and the host GPU override were preserved.
- The exact release reproduced public `@Tasty/shorts`404, fetched two positively verified
  Shorts from stable channel `UCJFp8uSYCjXOMnkUyb3CQ3Q` (`@buzzfeedtasty`), then replanned
  with the existing guarded homelab model at `10.0.10.23:8080` on copied data. The real model
  chose that stable Tasty ID. Equivalent failed handles remained filtered after persisted
  reload/replan. The verification report is retained beside the backup.
- Rollout registered the independently fetched Tasty metadata and observed bad-handle failure
  on the affected feed's plan. It preserved current planner content, family seeds, briefs and
  subscriptions. This was rehearsed on the disposable copy before application with services
  stopped; no hard-coded creator correction exists in application code.
- `DEPLOYED_COMMIT=2ec62a9`; API, worker and downloader run image `sha256:a3b5f9768135a580…`
  with zero restarts; API is healthy and no new service tracebacks were found. Public/local
  health, `/shorts`, `/agent`, `/settings`, live failed-reference suppression and canonical
  alternative availability passed. Integrity/profile/PIN/environment checks passed; every
  playlist field except its normal work timestamp matches the final backup. Saved media
  returned HTTP206 and decoded 20 frames. Baseline: six profiles, 7,213 videos, 267 content
  items and 267 capture jobs. Backup and rollback image are retained.

## Deployment record — playlist-scoped discovery (`a3dea76`, 2026-11-02)

- Profile subscriptions are now optional planner suggestions, with names, handles and up to
  three recent titles. Each playlist explicitly selects up to four current, owned, subscribed,
  unblocked channels. Low-trust blocked channels are excluded. Shorts selections browse the
  public Shorts tab; ordinary playlists use selected RSS sources. Explicit seed sources remain.
- Versioned plans invalidate previous unrestricted subscription rotations. A fingerprint of
  the playlist brief and source constraints invalidates generated queries, channels and
  subscriptions after edits. If replanning fails, only current explicit sources remain and
  their rotation keeps its position. User briefs, seeds and subscriptions are preserved.
- The title screen receives the entire brief and honors a valid all-off-topic result. Only
  actual model failures retain the existing full-judge fallback. RSS provenance now includes
  its channel ID; planning receives category-scoped search/channel/RSS outcomes, including
  recovered channel provenance for historical generic RSS rows.
- Independent review approved the final patch. Full backend suite: **580 passed, six skipped**;
  after the final context/failure-path refinements, **69 agent tests passed, one skipped**.
  Frontend check/build passed with the existing three CSS warnings; **10 agent/Shorts browser
  regressions passed**, including local-only playback and mobile pagination.
- Fresh online and stopped-service backups: `/root/compose/watchcraft-backups/20261102-pre-playlist-relevance`.
  Retained rollback image: `watchcraft:rollback-20261102-playlist-relevance` (previous `b03cce0`).
  The release initialized a restored database twice without changing original rows and passed
  network-isolated server startup, health and affected routes.
- Real configured-model rehearsal used only the verified existing private endpoint at
  `10.0.10.23:8080`, with a destination guard and isolated database. Cooking selected Sorted Food
  and Poppy Cooks; cooking Shorts selected Poppy Cooks. Hacker news produced technology-focused
  queries and three selected subscriptions. The report is retained beside the backup.
- Re-screened 13 historical pending RSS candidates on the isolated copy. Guarded rollout
  cleanup rejected only three unambiguously unrelated hardware clips in cooking Shorts,
  checking candidate ID, category, video, title, prior status and source before mutation.
  Accepted/history rows were retained; technology candidates under a terse Hacker news brief
  remain for full review rather than a manual interpretation of that brief.
- `DEPLOYED_COMMIT=a3dea76`. API, worker and downloader run image `sha256:72aad76a613defee…`
  with zero restarts; API is healthy. Public/local health, `/shorts`, `/agent`, `/settings`,
  live subscription-source policy across all enabled playlists, database integrity,
  profile/PIN/config checksums and saved-media HTTP206 plus 20 decoded frames passed.
  Stopped-service baseline: six profiles, 7,193 videos, 267 content items and 267 capture jobs.


## Deployment record — public Shorts discovery recovery (`b03cce0`, 2026-11-02)

- Reproduced public `@Tasty` channel auth-check errors and RSS404 for Level1Techs
  (`UC4w1YQAJMWOz4qtxinq55LQ`). Dedicated feed channel discovery incorrectly requested
  `/videos`; it now selects `/shorts`. Validated public channel tabs use yt-dlp's
  supported `youtubetab:skip=authcheck` option; private playlist/import/cookie-test
  extraction remains unchanged. Public channel URL validation preserves shared URLs
  and encoded handles while rejecting non-YouTube hosts and private feed paths.
- RSS unavailability now gets one public-channel fallback with a separate channel
  rate-limit turn. Blocking403/429 retains normal backoff. Failed RSS checks update
  their attempt timestamp so they cannot keep taking priority over other sources.
- New additive `video.shorts_verified` records positive public Shorts-tab/URL evidence.
  Full watch-page metadata cannot erase that classification on 60–180second Shorts;
  older duration-only guesses still remain correctable. Existing rows default to false.
- The cooking channel is `@buzzfeedtasty`, stable ID `UCJFp8uSYCjXOMnkUyb3CQ3Q`.
  `@Tasty` itself returned404 after the misleading auth gate was removed. Live search
  and the correct public Shorts tab confirmed identity. One affected Shorts discovery
  plan was repaired to use that verified ID, retaining its other fields and saving
  the old value in `tasty-plan-before.json` beside the backup. User seed preferences,
  real profile PINs, cookies and proxy configuration were not replaced.
- Lead review inspected the actual changes; a separate reviewer could not be started
  because the agent thread limit was reached. **575 backend tests passed, six skipped**.
  The actual release image fetched two verified Shorts each from Tasty and Level1Techs
  using copied production connection settings. Migration ran twice on an isolated fresh
  backup and preserved original video/feed/profile/settings fields. Network-isolated
  server startup, health and Shorts delivery passed. No frontend behavior changed.
- Backup: `/root/compose/watchcraft-backups/20261102-pre-channel-recovery`, including
  online/stopped-service SQLite, private configuration, source and data extras. Rollback
  tag: `watchcraft:rollback-20261102-channel-recovery` (previous `9820424`). The extra
  video column is additive and older images ignore it.
- `DEPLOYED_COMMIT=b03cce0`; API, worker and downloader use image `sha256:b135d1aa77c4c81c…`
  with zero restarts. Public/local health, database integrity and profile/PIN/environment
  checks pass. Final baseline: six profiles, 7,157 videos, 262 content items and 262 capture
  jobs. Existing independent/local-only feed playback stays unchanged.

## Deployment record — separate local-only feeds (`9820424`, 2026-11-02)

- Corrects the earlier mixed Shorts stream. `/shorts` opens an explicit feed picker.
  Settings → Feeds reuses playlist-style briefs, sources, language, thresholds and
  queue sizes with second-based short-video durations. Feeds have a separate category
  kind; existing categories remain playlists and are never silently converted.
- The feed API requires an owned feed, excludes watched/unaccepted/unfinished clips,
  and verifies a readable, nonempty local file inside the media root. The player uses
  only `/api/video/{id}/file`; Shaka, YouTube manifests, proxy playback, streaming fallback
  and streaming warmup links were removed from the feed flow. Missing files offer a
  local availability refresh. Category-style feed links redirect to the picker before
  requesting a playlist grid or streaming warmup. The unused floating cast control is
  absent from Shorts.
- Accepted feed clips enter the downloader even when regular playlist auto-downloads
  are manual. Master storage/agent controls, pause, quota and backoff remain effective.
  Dedicated feeds ignore Hide Shorts while regular playlists retain that preference.
  Collection can be paused while existing local clips remain playable.
- Independent cross-review approved backend and frontend changes. Backend: **554 passed,
  six skipped**, followed by **five focused tests**, including added ownership/PIN cases.
  Frontend check/build passed with the existing three CSS warnings. **25 focused settings
  and feed tests passed**. The broader browser run passed 142 tests with one opt-in skip
  and a malformed synthetic-touch fixture failure; after correcting the Touch identifiers
  and target, **all 10 feed acceptance tests passed**, including phone swipes/pagination,
  independently configured feeds, real local MP4 decoding and zero stream/proxy requests.
- Fresh backup: `/root/compose/watchcraft-backups/20261102-pre-local-feeds`; rollback image
  `watchcraft:rollback-20261102-local-feeds` retains the previous `ebc5588`. Online and
  stopped-service SQLite snapshots, source/private configuration and data extras are
  preserved. The additive migration ran twice on a fresh restored copy; all existing
  table fields and playlist defaults matched. The final image booted without external
  networking and served a local clip with HTTP 206, then decoded 20 frames successfully.
- Existing playlists are preserved. New feeds are configured explicitly through the picker
  or Settings → Feeds. Physical phone/TV acceptance is still deferred by the user and
  tracked in the external vault.
- Later rollback note: older images do not understand the new category kind. After real
  feeds have been created, pause their collection (`enabled=0` for `kind='shorts'`) before
  an older-image rollback so the old curator does not process them as ordinary playlists.
  Preserve the rows and local media; no destructive database restore is required.
- Production runs image `sha256:bf2118c07e85ab51…` on API, worker and downloader with
  zero restarts; `DEPLOYED_COMMIT=9820424`. Public/local health passed. Database integrity,
  original playlist kinds, profile/PIN checksums and `.env`/NVIDIA override checks passed.
  The stopped-service baseline preserves six profiles, 7,149 videos, 262 content items
  and 262 capture jobs. The live feed API requires a selection and initially lists no
  automatically invented feeds. Public Shorts/settings pages return 200; existing saved
  media returns HTTP 206 and decodes 20 frames.

## Deployment record — reviewed features (`ebc5588`, 2026-11-01)

- Reviewed `db3a28c..8ac84a1`: curated YouTube Shorts swipe feed, capture work in its
  own bounded lane, and semantic search reporting its comparison capacity. TikTok and
  other short-video providers are outside this shipped implementation and tracked in
  the external vault.
- Review fixes `624727b..ebc5588`: deduplicate Shorts in SQL before paging; exclude
  loaded IDs so finished clips cannot shift later pages; serialize player attachment
  and destruction, discard stale async completions, preserve playback retry, advance
  automatically at page boundaries, and keep delayed feedback tied to its original clip.
  Busy capture dispatch no longer consumes a retry attempt. Shorts manifests carry the
  selected profile only to the same-origin manifest endpoint. Fullscreen layout removes
  the shell sidebar; scoped keyboard-focus rules preserve fixed/absolute control positions.
- Independent review approved the final changes. Backend: **550 passed, six skipped**.
  Frontend check/build passed with the existing three CSS warnings. All **12 focused
  Shorts checks** passed, including actual FFmpeg DASH media with distinct decoded sizes,
  advancing playback time, manifest recovery, stale load/unmount races, feedback,
  pagination and phone gestures. Rendered screenshots were inspected. The complete
  browser suite passed **144 tests**, with one opt-in live-service test skipped, in 6.7 minutes.
- Fresh backup: `/root/compose/watchcraft-backups/20261101-pre-reviewed-features` with
  verified online/stopped-service SQLite snapshots, private configuration, source and
  data extras. Rollback tag: `watchcraft:rollback-20261101-reviewed-features` (previous
  `3b4a03c`). The final image initialized an isolated backup twice with unchanged profile,
  settings, video, content, capture and generated-subtitle rows. A network-isolated server
  passed health and `/shorts` startup checks; copied production feed queries returned
  12/1/0/0/0/12 unwatched Shorts without duplicates, and semantic capacity reporting passed.
- Deployment used rsync without deletion and preserved `.env` and `compose.host.yaml`.
  `DEPLOYED_COMMIT=ebc5588`; API, worker and downloader use image `sha256:28f7cd7d23e8aa31…`
  with zero restarts. Public/local health passed; database integrity is `ok`; profile/PIN
  checksums match the stopped-service baseline of six profiles, 7,116 videos, 223 content
  items and 223 capture jobs. The running image returned 12 eligible Shorts for profile 1;
  semantic capacity/lane reporting checks passed. Public `/shorts`, `/find` and `/team`
  returned 200; a saved video returned HTTP 206 and decoded 20 frames.
- Physical phone/TV acceptance remains deferred by the user and tracked externally.
  The Shorts feed follows existing playlist duration and Hide Shorts preferences; this
  release preserves those preferences. Disable Hide Shorts in Settings → Extras and allow
  short durations in playlists to keep curators collecting new clips.

## Deployment record — generated subtitles (`3b4a03c`, 2026-11-01)

- Release `8e62bcb..3b4a03c` adds a Subs menu with on-demand local STT generation,
  progress/retry and persistent alternative VTT tracks. YouTube captions and Off remain
  selectable. Settings → Player exposes the shared speech URL, model, masked key,
  language, enable switch and draft connection test; voice search uses those live settings.
- Verified service: `http://10.0.10.73:8011/v1`, model `Qwen/Qwen3-ASR-1.7B`, backed by
  `/root/compose/stt-qwen-wyoming`. No changes to that service. It rejects timestamped
  `verbose_json`, so this implementation uses short pause-aware chunks and explicitly
  labels approximate cue timing. One video runs at a time; five pending jobs, four-hour
  video limit, durable chunk progress, total request deadlines and bounded temporary audio.
- Validation: full backend suite **543 passed, six skipped**, then **14 generated-subtitle
  tests passed**, including extra deadline and saved-audio coverage. Frontend check/build
  passed with the existing three CSS warnings. Earlier integrated settings/player/voice
  checks passed 27 tests; final caption suite passed **six**, including the real Qwen service.
  A 15-second excerpt of an already saved public video was transcribed and displayed in
  the player. Screenshot review exposed dim captions behind controls; cue placement now
  moves above visible controls and was rechecked visually. The release container also
  generated real Qwen captions using disposable data.
- Fresh backup: `/root/compose/watchcraft-backups/20261101-pre-generated-subs`, including
  online and final stopped-service SQLite snapshots, private configuration, source and
  data extras. Rollback image: `watchcraft:rollback-20261101-generated-subs` (previous
  `693fd02`). Isolated backup initialized twice with the final image; original profile,
  settings, video and content rows were preserved. The new table is additive and older
  images ignore it; rollback does not require restoring the live database.
- Production rolled out with rsync without deletion, preserving `.env` and `compose.host.yaml`.
  `DEPLOYED_COMMIT=3b4a03c`; API, worker and downloader use image `sha256:5872492ac908ee86…`
  with zero restarts. Public/local health passed, database integrity is `ok`, and the final
  baseline retains six profiles, 7,097 videos, 223 content items and 223 capture jobs. Profile
  and PIN checksums match; effective speech endpoint/model availability was checked inside
  the live API. A saved video returned HTTP 206 and decoded 20 frames successfully.
- Physical TV/projector/phone acceptance remains deferred by the user and tracked in the
  external vault. Browser and service probes establish neither remote-control nor casting
  acceptance on those devices.

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

## Deployment record — public social sources (`a5f0e10`, 2026-11-02)

- Deployed Lemmy community API/RSS collection and explicit Mastodon links/statuses/tags trends, with source settings,
  sampled coverage, canonical identity, shared feed polling, host pacing/backoff and social capture/evidence context.
- Full backend release suite: 647 passed, 6 skipped. Frontend check/build passed with three existing CSS warnings;
  six focused Hub/source browser checks passed and the rendered source settings were inspected before release.
- Backup: `/root/compose/watchcraft-backups/20261102-pre-social-a5f0e10`; rollback image:
  `watchcraft:rollback-20261102-social` (previous `6e82ee9`). Retained online and final pre-rollout SQLite backups,
  configuration and prior source. Saved media was unchanged. Host NVIDIA overlay retained.
- Isolated restored copy initialized twice with original table counts and profile/PIN records preserved; network-isolated
  API startup and provider registry passed using a disposable adult QA profile through normal PIN unlock.
- API/worker/downloader run image `sha256:d8b33137875a5486f574bb6e291220eb771d46c762735677fbc7dc667c469602`,
  revision marker `a5f0e10`, with zero restarts at acceptance. Production database integrity/profile/PIN checksum,
  public health, sources/curator/Shorts/TV routes, actual deployed UI assets, saved-video HTTP 206 and two-second decode passed.
- The production browser preserved the child-profile Sources restriction. Adult source editing was verified in local
  browser tests and isolated API acceptance; real adult profiles remained PIN-protected. No production source lists,
  curator assignments or PINs were changed by deployment.
- Rollback: tag `watchcraft:rollback-20261102-social` as `watchcraft:latest` and use the existing three-file Compose
  command with `up -d --no-build --wait`; source/configuration snapshots remain in the backup directory.

## Deployment record — active curation and fresh editions (`43f647f`, 2026-11-04)

- News/memes collect hourly, Home Assistant every two hours, security every three and AI research every six.
  Master selection catches up every 15 minutes, resumes bounded batches and retries busy/failed model work.
  Discovery uses the worker; uncertain editorial decisions escalate to the judge. Existing quality thresholds remain.
- Editions exclude previous-day deliveries/read/archive/dismissed items and age stale candidates; same-day placements
  remain stable. Today refreshes across midnight/focus with profile-safe requests. Intro citation validation remains strict.
- Added Ars Technica, Home Assistant and Simon Willison RSS/Atom feeds; removed incompatible source assignments from
  general news and irrelevant assignments from Home Assistant. Atom updated timestamps now preserve article age.
- Validation: 816 backend tests passed, six skipped; 11 Today/News browser checks passed; frontend check/build passed
  with three existing CSS warnings. Independent review approved. Isolated restored database initialized and started twice.
- Backup `/root/compose/watchcraft-backups/20261104-curation`; rollback `watchcraft:rollback-20261104-curation`
  (previous `eb9ae84`). Prior curator/source assignments are in protected `curation-config-before.json` in that backup;
  application image rollback alone does not reverse cadence/feed configuration. Retained source/database/config backups.
- API/worker/downloader use image `sha256:0c86a5c45b47295080bd2049ef34776718fcb61ea7353fe7d1768a0a47304526`,
  revision `43f647f`, zero restarts at acceptance. Public health and SQLite quick_check passed; .env/host-overlay hashes
  unchanged. All five live collection runs completed successfully, finding 98 items. Current edition gained a cited
  introduction and removed yesterday's repeated entries. Thirty RSS articles discovered; two captured with ready AI
  summaries at acceptance, 28 queued for normal background processing. This is initial-cycle acceptance, not queue completion.

## Deployment record — manual master rerun (`30e1369`, 2026-11-04)

- Today has a profile-owned “Rerun master curator” control with durable pending/completed state, coalesced requests,
  ten-second visible progress polling and profile-safe responses. Explicit reruns release same-day seen-position locks
  and regenerate selection/introduction after successful review, preserving delivery history and quality thresholds.
  Both nightly and normal dispatch consume manual requests; model errors, busy models and empty responses retry durably.
- Validation: 823 backend tests passed, six skipped; frontend check/build passed (three existing CSS warnings), all 11
  Today browser checks passed. Phone screenshot inspected and independent review approved lifecycle fixes.
- Backup `/root/compose/watchcraft-backups/20261104-manual-30e1369`; rollback image
  `watchcraft:rollback-20261104-manual` (previous `43f647f`). Online and final stopped-service backups retained.
  Restored copy preserved counts/profiles/PINs, initialized successfully and started twice with isolated networking.
- API/worker/downloader image `sha256:c8e984e4e80f756bb2f1010866e23e4ed20812e952aac11088d40ebda39f0621`,
  revision `30e1369`, zero restarts at acceptance. Public health/Today/News, deployed button assets, database integrity,
  profile/PIN preservation and host configuration hashes passed. Manual refresh requested for the active Hub profile.

## Deployment record — Feeds naming (`9b48d7e`, 2026-11-04)

- Renamed the Shorts section to Feeds in navigation, document title, settings links, player/exit labels and accessibility
  names. Extras describes the filtered content as short videos. Existing `/shorts` links and stored/API feed kinds remain
  compatible; the missing-selection API error now says “Select a feed”.
- Validation: nine focused backend tests, all 21 feed browser regressions, frontend check/build passed (three existing
  CSS warnings). Isolated restored database preserved counts/profiles/PINs and the application started twice; deployed
  Feeds assets and route checked before rollout.
- Backup `/root/compose/watchcraft-backups/20261104-feeds-9b48d7e`; rollback image
  `watchcraft:rollback-20261104-feeds` (previous `30e1369`). Configuration hashes retained and unchanged.
- Post-rollout public health/routes, database integrity and exact profile/PIN preservation passed. Live browser confirmed
  “Feeds · WatchCraft”, Feeds navigation, Leave Feeds and the main Feeds link. All services running with zero restarts.

## Deployment record — public article sharing (`171a6a5`, 2026-11-04)

- Article details and news-story source archives have an explicit Share article control. It creates a random 256-bit
  public capability, shows a copyable URL and permits revocation. Existing links are stable until revoked; regenerated
  links use a new token. Creation requires authenticated Hub access and ownership of the selected article.
- Public pages are standalone read-only HTML, available without household login or profile PIN. Only source title,
  publisher/author, original URL, archived text, stored raster image and AI summary are exposed. Notes, reading state,
  curator details, collections and other enrichment fields are excluded. HTML is escaped; restrictive CSP, no-referrer,
  no-store and noindex headers apply. Images require the same capability; active SVG content is excluded.
- Revocation immediately disables page and image. Profile removal or lost Hub/source visibility disables public delivery;
  a profile owner can still manage an existing token after discovery removal. Responses and UI requests are profile scoped.
- Validation: 829 backend tests passed/six skipped, final six focused sharing security checks passed, frontend check/build
  passed with three existing CSS warnings, four sharing/news browser checks passed. Desktop/phone layout inspected and
  independent review approved. Restored-copy initialization preserved counts/profiles/PINs; isolated image verified
  anonymous text/image/summary delivery behind a household password, restart persistence and page/image revocation.
- Backup `/root/compose/watchcraft-backups/20261104-sharing-171a6a5`; rollback image
  `watchcraft:rollback-20261104-sharing` (previous `9b48d7e`). Additive ArticleShare table is ignored by older images;
  an image rollback removes the public route and controls, retaining stored tokens for a subsequent compatible release.
- Production API/worker/downloader image `sha256:d0a5e1e308addd659ae01bdc9db5c1338d936f38a571db41156525269141aea3`,
  revision `171a6a5`, zero restarts. Public health/routes, sharing UI assets, unknown-token rejection with no-store headers,
  integrity/profile/PIN/configuration preservation and two-second saved-video decode passed. No production article was
  published automatically during validation; public sharing requires the reader's explicit per-article action.

## Deployment record — cleaner article archives (`baa7605`, 2026-11-04)

- HTML extraction prefers semantic publisher article bodies and removes structural newsletter/related/share/promotion
  widgets. Generic pages retain a fallback; quotations, following paragraphs, lists and preformatted code remain.
  The actual Guardian Hinton page extracts to 5,374 characters and retains the final Channel 4 airing paragraph.
- News/article/web captures now receive a worker cleanup review before summarisation. Workers may only remove whole
  indexed clutter paragraphs, never rewrite retained reporting. Strict result validation, a 40% removal guard and
  atomic fallback retain original text on errors. ArticleText preserves original extracted source separately.
- Existing readable archives enter bounded local-only review batches through normal durable capture jobs. Eligibility
  is filtered before batch limits; already-successful cleanup is reused on retries so errors cannot cumulatively trim
  text. New captures retain scheduling priority. Shared reading pages render separate escaped paragraphs.
- Validation: broad backend suite 858 passed/six skipped, 50 final focused extraction/worker/capture/sharing checks,
  four sharing/news browser checks. Independent review approved retry/backfill fixes. Real worker reviewed the old
  public example: 39 clutter paragraphs removed, 7,525 to 5,643 characters; article ending retained. No production
  content was changed by that probe. Restored image initialized with counts/profiles/PINs preserved, started twice,
  verified real Guardian extraction and retained the existing shared URL.
- Backup `/root/compose/watchcraft-backups/20261104-cleanup-baa7605`; rollback image
  `watchcraft:rollback-20261104-cleanup` (previous `171a6a5`). ArticleText is an additive table ignored by older images;
  original extracted text remains available for deliberate content restoration if reverting cleanup results.
- Final local-only attachment safeguard deployed as `c210238` (range `baa7605..c210238`); 51 final focused checks passed.
  Same isolated restore and twice-started checks passed for this exact image. Pre-follow-up data retained separately as
  `before-c210238.sqlite3`; initial pre-cleanup backup remains intact. Final API/worker/downloader image:
  `sha256:4ae32672adea96fb579688802e49097f0ee2011681f24d4efdee6b84253e6fd4`, zero restarts at acceptance.
- Refreshed the reported shared Guardian archive through normal capture/worker/summary: complete, cleanup reviewed,
  summary ready, reading text 5,314 characters, original 7,525 characters retained. Live browser inspection confirmed
  clean paragraphs, stored image and summary with the final article paragraph intact and newsletter/topics/cards gone.
  Initial backfill snapshot: three reviewed/47 queued. Remaining existing articles continue asynchronously.
- Production health/routes, integrity/profile/PIN/configuration preservation and saved-video two-second decode passed.

## Deployment record — always-captioned Reels and subtitle daemon (`2549876`, 2026-11-04)

- Added dedicated `bt-subtitles`/`wc-subtitles` service using the configured household STT endpoint. It runs without
  viewers, automatically fills four queue slots from readable, accepted, enabled local short-video feeds and reserves
  a fifth slot for viewing requests. Requested clips get priority. Stored valid VTT is reused; no automatic remote
  audio fallback is permitted for Reel jobs, including explicit retries and job upgrades.
- Daemon heartbeat remains responsive during transcription; Compose restarts it unless stopped. Interrupted jobs
  preserve completed chunks. Temporary connection/timeout/5xx/429 failures retry after 1/5/15 minutes; permanent
  no-speech/unreadable-audio/auth errors remain explicit failures. Additive local_only/retry_count/retry_at migration
  retains existing tracks. API's embedded subtitle worker is disabled in Compose to keep one dedicated consumer.
- Reels wait for a nonempty validated VTT before setting video source or playing. Captions render as an always-on,
  plain-text overlay; clips start muted and have an explicit unmute control. Progress, bounded polling, regeneration,
  retry and skip remain available with main navigation. Profile/clip races cannot start stale playback. Long phone
  captions sit above feedback controls and scrollable details. Timing is approximate (six-second local audio chunks).
- Validation: broad backend suite 881 passed/six skipped, final 52 subtitle/daemon/feed/provenance checks passed;
  frontend check/build passed with three existing CSS warnings; 26 browser checks plus final caption and actual-video
  layout checks passed. Independent review approved local-only, validation, daemon and durable-retry corrections.
- Actual configured Qwen STT generated a cached track for a 47.23-second local Reel (four original chunks, 1,025-byte
  VTT). Exact image on isolated restored DB preserved counts/profiles/PINs and API/daemon started twice. Real video
  and STT captions were played through a temporary SSH-only connection, media mounted read-only and transcription
  disabled on the disposable copy; browser confirmed muted=true, paused=false, advancing time and visible captions.
- Backup `/root/compose/watchcraft-backups/20261104-reels-2549876`; rollback image
  `watchcraft:rollback-20261104-reels` (previous `c210238`). Rollback script stops/removes the new daemon, retains a
  fresh pre-rollback DB, pauses local-only pending jobs so the old worker cannot fetch remote audio, restores the old
  Compose files and three-service topology, then starts the retained image. Schema additions are backward compatible.
- Production API/worker/downloader/subtitle daemon all use
  `sha256:e95971328d0de849b143d3c189f02bd216032f71e6e7ad4f95fc6634b6598038`, revision `2549876`, zero restarts.
  wc-subtitles healthy, /media read-only; wc-api embedded worker disabled. Public health/routes, database integrity,
  exact profile/PIN preservation, host config hashes and saved-video decode passed. Without a viewer, the new daemon
  generated four local-only ready tracks and had one running at 53.7% plus three queued at initial acceptance.
  Existing missing-caption backlog continues; clips remain playback-gated until their captions are ready.

## Deployment record — subtitle monitoring in Stats (`da6b9f4`, 2026-11-04)

- Added Reel subtitles coverage, queued/running/missing/retry/failure counts, active progress and daemon heartbeat
  to `/stats`. Visible pages refresh every ten seconds, coalescing overlapping requests. Admins see household
  totals; other profiles see their own jobs. Responses are private/no-store; locking or switching clears old data.
- Coverage counts distinct eligible unwatched local Reels, validates stored VTT and sanitizes failure messages.
  Heartbeat time is measured after file inspection to avoid a concurrent daemon update appearing stale.
- Validation: 45 focused backend checks, two final heartbeat regression tests, frontend check/build and eight
  Stats browser checks passed. Independent privacy/scoping review approved. Exact final image passed isolated
  restore/init/restart checks with media read-only and network disabled. Live Family Stats layout and fresh
  heartbeat verified; household totals checked server-side without changing or entering production PINs.
- API-only deployment: wc-api uses sha256:b914ec1ebfc1535d0b1fff46f493fc1acaf664aa69345eef9fe361fa9f32711c.
  Existing worker/downloader/subtitle daemon retain the prior 2549876 image. Subtitle daemon start time was
  unchanged throughout deployment. Public health/routes, database integrity, profiles/PINs, config hashes and
  saved-media decode passed.
- Backup `/root/compose/watchcraft-backups/20261104-stats-0891f9a`; rollback tag
  `watchcraft:rollback-20261104-stats`. API rollback uses no-deps recreation; subtitle generation stays running.
