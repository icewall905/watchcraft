# Build contract & tickets

Shared reference for every agent working on this repo. `docs/PLAN.md` = approved design.
Repo: `git@github.com:icewall905/bt.git` (local `/home/hnyg/git/bt`). App code name: **bt** (BetterTube).
Deploy: `root@10.0.10.73:/root/compose/bt`, port **6645**, public URL `https://bt.tdgs.org` (rproxy 10.0.10.60).

## Changes after plan approval (user requests)
- **Profiles** (family members): switcher top-right, no PIN. Profile owns categories, feedback,
  lessons, subscriptions/blocks, watch history. Frontend sends `X-Profile-Id: <id>` header on every
  API call (stored in localStorage `bt.profile`). In accounts mode the id is validated against the signed-in
  account (403 for another account's profile); `X-Account: <public_id>` picks which signed-in account a tab uses.
  Without the header the backend falls back to the account's first profile. See `docs/ACCOUNTS.md`.
- **Pause AI** button always visible (status pill in top bar + Agent page): `POST /api/agent/pause`
  / `/api/agent/resume` (`/api/agent/reset-backoff` separately clears a bot-check backoff). When paused the worker makes **no YouTube and no LLM calls**.
- **Queue-aware curation (diversity)**: the planner and the judge both receive the current category queue
  (`queue_titles`: accepted, not dismissed, not finished-watching, newest judged first, ≤15 titles) with a VARIETY
  instruction — plan searches for underrepresented topics from the brief, and judge borderline videos stricter when the
  queue already holds several very similar ones / kinder when they fill a gap. Quality always wins over variety.
- **"Seen it"** (`POST /api/history/seen {video_id}`): marks the video finished-watched for the active profile and
  dismisses its candidates in the profile's categories; the prefilter already rejected watched videos, and dismissed
  candidates are skipped by enrichment, judging and the download queue. Button on category cards.
- **"Don't like channel"** (`POST /api/channels/{id}/block`): blocks the channel for the profile and dismisses its
  accepted/pending candidates in the profile's categories (blocked channels are rejected by the prefilter). Button on
  category cards; the watch page "Block channel" uses the same endpoint.
- **Verdict feedback** (candidate drawer, opened from Agent-page verdicts and "Why picked?"): the 💬 Feedback button
  opens the shared feedback dialog (`onDone(rating)` callback). A dislike now also flips the candidate to
  `status="rejected"` + dismissed with `judged_at`, appends the family's reason to `red_flags` ("Family: chips, text")
  and emits a `reject` AgentEvent ("🚫 You rejected: ... — reason", payload has `candidate_id` + `by:"family"`).
  The judge and planner receive **RECENT REJECTIONS with reasons** (`data.rejections_for`: last 8 rejected candidates
  per category; the family's dislike reason beats the AI's verdict note), so future picks avoid the same failure
  ("this Ollama video was too basic → want advanced ones").
- **Downloads follow verdicts**: a video rejected by Family (dislike) or Judge, or finished-watched by any profile
  ("Seen it" / ≥92 % watched), must not be downloaded and its copy is removed. `sync_queue` never queues watched
  videos; `drop_unwanted` (hooks on the feedback + history + seen endpoints) immediately evicts the queue row and
  deletes the saved file — an in-flight download aborts within ~2 s (hook checks `_unwanted`). The downloader runs a
  `purge_unwanted` sweep every iteration (even while paused/disabled/backoff) as the safety net. Pinned
  ("Save offline") copies are never removed automatically, and a copy with a file request in the last 10 min is left
  for a later sweep (someone may be streaming it). Evicted rows are not re-queued automatically (manual "Save offline"
  queues them again; the video streams otherwise).
- **Hover previews**: hovering any video thumbnail (cards, candidate drawer, agent activity/verdicts,
  status pill, stats explorer, storage list, up-next, home hero) silently autoplays a muted, looping
  preview: the local copy when saved offline, otherwise DASH at ≤540p through the same proxy as the
  player (progressive muxed streams no longer exist on YouTube, so `/api/video/{id}/fallback` is not
  usable for previews). One preview at a time; sweeps under 350 ms start nothing; teardown on leave
  aborts the stream. The action survives feed refreshes under a parked pointer (agent 'accept' events
  reload home/category rows constantly) and re-arms re-created cards. Shared by `use:preview`
  (`$lib/preview.ts`); touch-only devices are skipped.
- **Statistics page** (`/stats`, top-bar tab "Stats"): `GET /api/stats/page?days=N` (N ≤ 120, default 30) returns
  `overview` (videos known, candidate pipeline counts incl. `waiting_judge`/`in_queue`/`rejected_ai` vs
  `rejected_family`/`dismissed`, downloads by status + storage, feedback by rating, lessons, watched totals + seconds,
  channel counts, YT budget), `categories` (per-category counts incl. in-queue vs target, watched, avg accepted score)
  and `daily` (per-day: found/accepted/rejected split family-vs-AI/feedback/watched with per-category breakdown/
  downloads/YT requests, oldest first, UTC days).
- Build material lives in this repo; commit when ready (no AI attribution trailers in commits).

## Layout / ownership
```
backend/
  requirements.txt            pinned deps (py 3.12)
  bt/config.py                env + DEFAULT_SETTINGS                     [lead, done]
  bt/models.py                SQLModel tables                             [lead, done]
  bt/db.py                    engine, settings, agent state/control, emit(), log_yt_request() [lead, done]
  bt/auth.py                  accounts auth middleware, sessions (docs/ACCOUNTS.md) [lead, done]
  bt/main.py                  app, routers, SPA serving                   [lead, done]
  bt/api/core.py              profiles/categories/feeds/channels/feedback/lessons/history/settings [lead, done]
  bt/api/agent.py             agent status/control/SSE/llm-test          [lead, done]
  bt/ytdl.py                  yt-dlp wrapper                              [ticket A]
  bt/feeds.py                 channel RSS                                 [ticket A]
  bt/streams.py               DASH MPD + signed range proxy               [ticket A]
  bt/extras.py                SponsorBlock, RYD, DeArrow clients          [ticket A]
  bt/api/video.py             router: search/video/streams/proxy/thumb/channel [ticket A]
  bt/llm.py                   OpenAI-compatible chat client               [ticket B]
  bt/agent/*.py               worker: __main__, loop, ratelimit, planner, enrich, judge, learn [ticket B]
  tests/                      pytest                                      [A: test_streams/test_ytdl, B: test_agent]
frontend/                     SvelteKit SPA (adapter-static) + PWA        [ticket C]
Dockerfile, compose.yaml, host/  packaging + desktop integration           [lead]
```
Do not edit files owned by another ticket. If you need a change there, describe it in your report.

## Conventions
- DB access: `from bt.db import session` (`with session() as s:`), FastAPI dependency `get_session`.
- Settings: `get_settings()` returns merged dict, keys: `llm`, `throttle`, `agent`, `player`,
  `sponsorblock`, `extras` (see `config.DEFAULT_SETTINGS`).
- Agent visibility: `emit(kind, message, level, category_id=, video_id=, profile_id=, payload=)`;
  current activity: `set_agent_state(state=..., detail=..., ...)`.
- Every outbound YouTube request: `log_yt_request(kind, origin="worker"|"user", ok, detail)`.
- Times are **timezone-aware UTC** (`models.now()`); sqlmodel 0.0.48 rejects naive datetimes. Store ISO strings with offset in JSON (`now().isoformat()`).

## ytdl.py interface (A implements, B consumes) — all **sync**, worker calls them in a thread
```python
class YtError(Exception): ...
class YtBlocked(YtError): ...      # 429, "Sign in to confirm you're not a bot", captcha, HTTP 403 on API
class YtUnavailable(YtError): ...  # private/removed/age-gated without cookies/members-only

def search(query: str, n: int = 15, origin: str = "worker") -> list[dict]     # flat, cheap
def channel_videos(ref: str, n: int = 30, origin: str = "worker") -> tuple[dict, list[dict]]
    # ref = UC id | @handle | URL ; returns (channel{id,name,handle,avatar}, entries)
def video_info(video_id: str, origin: str = "worker") -> dict   # full yt-dlp info, in-memory TTL cache 4h
def upsert_video(s, info_or_entry: dict) -> Video               # map yt-dlp dict -> Video row (no commit)
def transcript(info: dict, max_chars: int, langs: list[str] | None = None) -> str
    # manual subs preferred over auto; json3/vtt -> plain text; "" if none; requests logged
def comments(video_id: str, max_comments: int = 40, origin: str = "worker") -> list[dict]
    # top-sorted, [{author, text, likes}], no replies
```
Entry dict (search/channel): `{id,title,channel_id,channel_name,duration_s,view_count,thumbnail,published_at|None,is_short,is_live}`.
yt-dlp options: PO-token provider plugin `bgutil-ytdlp-pot-provider` with
`extractor_args={"youtubepot-bgutilhttp": {"base_url": [POT_URL]}}`; cookiefile = `COOKIES_PATH` if exists;
quiet, no download. Shorts: duration ≤ 60 s or `/shorts/` url.

## api/video.py endpoints (A)
| method | path | returns |
|---|---|---|
| GET | `/api/search?q=&n=20` | `[video_out]` (origin=user, upserts Video rows) |
| GET | `/api/video/{id}` | `video_out(full)` + `channel{id,name,avatar,subscribed,blocked}` + `dislikes` (RYD, optional) + `dearrow{title,thumb}` (if enabled) + `in_categories:[{category_id,candidate_id,score,summary,reasons}]` for active profile + `history{position_s,finished}` |
| GET | `/api/video/{id}/manifest.mpd` | DASH MPD (all segment URLs → signed `/api/proxy/...`), `?max_height=` |
| GET | `/api/video/{id}/fallback` | 302 → signed proxy URL of best progressive (muxed) format |
| GET | `/api/proxy/{token}` | streaming range proxy to googlevideo (Range passthrough, 206, chunked). token = itsdangerous-signed URL with expiry; never an open proxy |
| GET | `/api/video/{id}/captions` | `[{lang,name,auto,url:"/api/video/{id}/captions/{lang}.vtt"}]` |
| GET | `/api/video/{id}/captions/{lang}.vtt` | WebVTT |
| GET | `/api/video/{id}/sponsorblock` | `[{segment:[start,end],category,actionType,UUID}]` (k-anonymous sha256 prefix API, cached 1h, `[]` on error) |
| GET | `/api/video/{id}/comments?n=40` | `[{author,text,likes}]` |
| GET | `/api/thumb/{id}?q=hq|mq|maxres` | cached image (disk cache under DATA_DIR/thumbs, long cache headers) |
| GET | `/api/channel/{ref}` | `{channel, videos:[video_out]}` |
| GET | `/api/channel/{id}/avatar` | cached avatar image |

`video_out` lives in `api/core.py` (import it). Live streams: return 409 `{"detail":"live not supported"}` from manifest (v1).

## Agent worker (B): `python -m bt.agent`
Loop per docs/PLAN.md "AI curator": plan → discover → enrich → judge → learn. Requirements:
- Reads control each iteration: `paused` (sleep, state=`paused`), `run_now` (skip sleep, clear it), `playing_until`
  (+ throttle.pause_while_playing → state `waiting`), `backoff_reset`.
- `agent.enabled` setting false → idle.
- Rate limiter (`agent/ratelimit.py`): single YT request at a time, jittered delay min..max, per-hour/day caps
  computed from `YtRequest` table (worker origin), circuit breaker on `YtBlocked` with exp backoff
  backoff_min..backoff_max persisted in `agent_state` (`state=backoff`, `until`). Quiet hours.
- During every wait: `set_agent_state(state="sleeping", detail=..., until=iso, category_id=...)` so UI can count down.
- States: `offline | idle | paused | waiting | sleeping | backoff | planning | searching | fetching | transcript | comments | judging | learning | error`.
- Category picking: enabled categories across **all profiles**, weight = priority × deficit (target_queue − accepted unwatched),
  skip if deficit ≤ 0, prefer least-recently worked; process `found`→`enriched`→judged backlog before new searches.
- Planner prompt includes category brief, seed queries/channels, include/exclude keywords, lessons (profile+category scoped,
  active), recent accepted/rejected titles, recent dislike feedback. Output JSON `{queries:[...], channels:[...]}`. Also uses
  subscribed channel RSS (`feeds.channel_feed`) and seed_channels via `channel_videos`.
- Prefilter before enrich: duration range, is_short (if hide_shorts), is_live, exclude keywords in title, blocked channels for the
  profile, already candidate in this category, max_age_days.
- Enrich: `video_info` + `transcript` + `comments`, stored on Video (enriched_at), reused across categories.
- Judge prompt: category brief, lessons, channel trust, metadata, description, chapters, transcript (truncated), top comments,
  thumbnail as image (`image_url` = data URL of `/thumb` bytes) when `llm.vision`. Strict JSON:
  `{score:int 0-100, summary:str (1-2 sentences, why fun/relevant), reasons:[str], red_flags:[str], kid_safe:bool, tags:[str]}`.
  kid_safe category → reject if `kid_safe` false. Accept if score ≥ category.threshold.
- Learn: unprocessed Feedback → LLM distills ≤3 lesson rows (merge/dedupe with existing lessons; may set channel_id), adjust
  ProfileChannel.trust (+0.2 like/love, −0.3 dislike, clamp −1..1; trust ≤ −0.8 ⇒ treated as blocked by prefilter). Mark processed.
  Learning has priority over searching (runs as soon as feedback arrives, no YT calls).
- LLM failures: retry ×2, then leave candidate in `enriched` and back off LLM for 5 min (emit warn). Parse JSON robustly
  (strip ```json fences / <think> blocks, take first {...}).
- Emit an AgentEvent for every meaningful step with human text, e.g. `🔎 Searching "mark rober egg drop" for Kids`,
  `📜 Reading transcript: <title>`, `💬 Reading 40 comments`, `✅ Accepted (82): <title> — <summary>`,
  `❌ Rejected (31): <title> — <red flag>`, `🧠 Learned: "Avoid ..."`, `⏸️ Paused`, `🛑 YouTube bot-check — backing off 1h`.

## Frontend (C)
See ticket C brief (in agent prompt / `frontend/README.md` once written). API above + `api/core.py` + `api/agent.py`.
SSE: `GET /api/agent/stream` events `state` and `agent`.

## Ticket D — local video cache (yt-dlp downloads)
User request: download the videos the AI selects and play them locally.
- Storage: host `/mnt/local/localstorage/bt` (NFS, ~2.7 TB free) mounted at `/media` in bt-api + bt-downloader
  (`BT_MEDIA_DIR=/media`). Layout `/media/<id[:2]>/<id>.mp4` (+ `.info.json` optional). Write to `*.part` then rename.
- Policy: **all accepted (non-dismissed) candidates** are auto-downloaded, highest score / newest first, round-robin across
  categories, until `cache.max_gb` (default 500). Manual "Save offline" on any video (`pinned`). Eviction when over cap: LRU among
  non-pinned, preferring videos finished by every profile that watched them, then dismissed/rejected ones, then oldest last_played.
  Dismiss/dislike → eligible for eviction; dislike-rejected and finished-watched videos are evicted at once (see "Downloads follow
  verdicts" above), not only under cap pressure. Deleting a category doesn't delete files immediately; eviction handles it.
- Format: single merged MP4, H.264 (avc1) + AAC, ≤ `cache.max_height` (default 1080), via yt-dlp
  `bv*[vcodec^=avc1][height<=H]+ba[acodec^=mp4a]/b[ext=mp4]` with ffmpeg merge, `--merge-output-format mp4`, faststart
  (`-movflags +faststart` postprocessor args). ffmpeg added to the image. PO token + cookies same as ytdl.py.
- Process: separate service `bt-downloader` (`python -m bt.downloader`), same image. One download at a time, `cache.rate_limit`
  bytes/s (default 8 MB/s), jittered pause between downloads (`cache.min_gap_s` default 30). Respects agent control
  `paused` (stop/abort current download, resume later — yt-dlp `continuedl`), `agent.enabled`, the worker's YouTube backoff
  (`agent_state.backoff_until`) and `cache.enabled`. Each download logged as YtRequest kind "download" origin "worker"
  (counts toward hourly cap; skip when cap reached). Emits AgentEvents kind "download" (📥 Downloading…, ✅ Saved offline (1080p,
  512 MB), 🗑️ Evicted …) and writes its own state in Setting `downloader_state` {state, video_id, title, progress 0..1,
  speed, eta, used_bytes, max_bytes, queue}.
- Model `Download` (table): video_id pk, status queued|downloading|done|error|evicted, path, size_bytes, height, vcodec,
  progress, error, attempts, pinned, requested_by_profile, created_at, finished_at, last_played_at.
- Settings key `cache`: {enabled: true, max_gb: 500, max_height: 1080, rate_limit: 8000000, min_gap_s: 30, auto: "accepted"}.
- API (api/downloads.py): `GET /api/downloads` (list + totals + downloader_state), `POST /api/downloads` {video_id} (pin/queue),
  `DELETE /api/downloads/{video_id}` (delete file, mark evicted, unpin), `GET /api/video/{id}/file` (FileResponse with Range,
  only when done; update last_played_at). `GET /api/video/{id}` gains `download: {status, progress, height, size_bytes, pinned}`.
  `video_out` gains `offline: bool`. Agent SSE `state` event gains `downloader` (downloader_state).
- Player: if `download.status == done` play `/api/video/{id}/file` directly (no Shaka manifest; quality menu shows "Offline
  1080p"); on error fall back to streaming. Captions/SponsorBlock unchanged.
- UI: "Save offline"/"Remove download" in info panel + card menus; offline badge on cards; Settings → "Storage" tab
  (enable, cap slider, max height, rate limit, usage bar, list of downloads with progress/delete, "Clear all"); Agent page shows
  downloader status/progress and queue; top-bar pill shows a small download progress ring when downloading.
