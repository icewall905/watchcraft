# WatchCraft Hub — "Watching the Internet for you."

Instead of going on the internet yourself, you configure WatchCraft to go on the internet
for you: curators and research briefs search, filter, organise and summarise locally.
Video stays front and centre (TV is the default landing page).

## A. Gap analysis (audit of 125b1fc)

| Area | State before this iteration |
|---|---|
| TV home `/`, video search `/search`, subscriptions, watch, Video AI `/agent`, stats | Working; untouched |
| Sidebar | 6 flat links duplicated in one component; TV drawer = same list |
| Device modes | `desktop`/`tv` per browser (localStorage); no mobile layout beyond icon rail |
| Curators | One source per curator (`rss`/`github`), create + pause/resume/run; no edit/delete/history |
| Discoveries | `discovery` table mixes content and profile state; first curator only; no types |
| Library | A toggle on the Discover page |
| News, Knowledge, Briefings, Alerts, Sources, Research/OSINT, global search, `/tv` | Missing |
| Fetch security | Solid: HTTPS-only, public-IP pinning, redirect revalidation, size limits, no DTDs |
| AI for non-video | None (stored notes only) |

## B. Route / navigation map

Central config: `frontend/src/lib/navigation.ts`.

```
WATCH        TV (/tv, alias of /)  · Subscriptions (/subscriptions)
EXPLORE      Discover (/discover) · News (/news) · Knowledge (/knowledge) · Library (/library)
             Briefings (/briefings) · Alerts (/alerts)
RESEARCH     Research / OSINT (/research)
AUTOMATION   Curators (/curators) · Sources (/sources) · Video AI (/agent) · Statistics (/stats)
footer       Settings (/settings) · profile
global       Find (/find, Ctrl/Cmd+K) · item detail (/item/[id])
```

- TV drawer (tv mode): TV Home, Video search, Subscriptions, Playlists (settings), Hub (→ /discover), Settings.
- Mobile (desktop mode, ≤760px): bottom bar TV · Discover · Library · Alerts · More (sheet with the rest).
- Pinned: collections / topics / saved searches with `pinned=true` appear under "Pinned".

## C. Content types and provider registry

Types (`GET /api/hub/registry`): `video, article, news, web, recipe, pattern, advisory, project, document, person, organization, domain`.
Type-specific data lives in `ContentItem.meta`; AI output lives in `ContentItem.enrichment` (never mixed).

| Provider | Mode | Input | Notes |
|---|---|---|---|
| `rss` | feed | URL | RSS/Atom, incl. YouTube channel feeds → `video` |
| `github_releases` | feed | repo URL | public releases |
| `github_search` | query | — | repository search |
| `hackernews` | query | — | Algolia HN API |
| `arxiv` | query | — | arXiv Atom API |
| `nvd` | query | — | NVD CVE 2.0 API; authoritative CVSS |
| `cisa_kev` | feed | — | Known Exploited Vulnerabilities, keyword-filtered |
| `wikipedia` | query | — | encyclopaedic background (OSINT) |
| `web_search` | query | — | SearXNG (self-hosted) or Brave API; unavailable until configured |
| `rdap` | query | domain | domain registration (OSINT) |
| `page` | url | URL | generic page extraction (OpenGraph, JSON-LD Recipe/Article) |

A **Source** is a reusable row (`provider` + optional `url`) shared by many curators.
Query providers run each curator query; feed providers ignore queries and are keyword-filtered.

## D. Checklist

P0 (this iteration): nav config, sidebar/mobile/TV drawer, `/tv`, all section routes with
loading/empty/error/populated states, Discover without admin form. ✅ when listed in §F.

P1 (this iteration): content model + migration v2, sources registry, multi-source curators,
edit/duplicate/delete, run history, FTS5 global search, web search provider, page extraction,
library/collections, item detail, provenance, AI relevance (optional), Research/OSINT.

P2 (partly this iteration): news grouping, knowledge topics + notes, NVD/CISA/arXiv/HN,
briefings, alerts, saved searches, templates. Later: advanced schedules, child restrictions.

P3 (later): semantic search, phone→TV handoff, custom sidebar ordering, offline reading,
notification channels, export/import of curator configs beyond JSON download.

## E. Migration and compatibility

- `watchcraft_schema_version` 1 → 2, in `bt/hub/migrate.py`, inside `init_db`'s `BEGIN IMMEDIATE`.
- New tables are created by `create_all`; new `curator` columns are added with `ALTER TABLE ADD COLUMN`
  (idempotent: checks `PRAGMA table_info`).
- Each v1 curator gets a `Source` from its `provider/source` and a `CuratorSource` link.
- Each v1 `discovery` row becomes `ContentItem` (by canonical URL) + `CuratorDiscovery` +
  `ItemState(saved)`. The v1 `discovery` table is kept read-only for rollback.
- Rollback: restore the pre-upgrade backup (maintenance menu). v1 code ignores new tables/columns.
- Video tables are not touched.

## API contract (`/api/hub`, profile from `X-Profile-Id`)

All list endpoints return `{items, total}` unless noted. Times are unix seconds (float).

```
GET  registry                         {types:[{key,label,icon}], providers:[{key,label,mode,needs_url,capabilities[],available,reason}]}
GET  counts                           {discover_unread, alerts_new, news_today}

GET  items?section=discover|news|library&q&type&provider&curator&saved&unread&favorite&read_later
          &archived&min_score&since&until&sort=newest|published|relevance&collection&topic&offset&limit
GET  items/{id}                       Item + {curators:[{id,name,found_at,score,reason}], collections:[id], related:[Item]}
POST items/{id}/state                 body partial {saved,favorite,read_later,archived,dismissed,read,notes,tags,rating}
POST items/import                     {url}  → Item (page extraction, saved=true)
GET  news?days=1|7|30&topic          {groups:[{key,title,items:[Item],latest}], topics:[Topic]}

Item = {id,url,type,title,summary,author,publisher,image,published_at,first_seen,provider,meta,enrichment,
        state:{saved,favorite,read_later,archived,dismissed,read_at,notes,tags,rating},
        score,reason,curator_names[]}

GET/POST         collections          {id,name,description,icon,color,view,pinned,count}
PATCH/DELETE     collections/{id}
POST/DELETE      collections/{id}/items/{item_id}

GET/POST         sources              {id,provider,name,url,enabled,last_success,last_error,last_checked,item_count,curator_ids[],
                                       config{},configurable,last_repair,health{status,signals[],success_rate,empty_rate,
                                       consecutive_failures,typical_count,trend}}
PATCH/DELETE     sources/{id}
POST             sources/{id}/test    {ok,count,sample:[{title,url,published_at,excerpt}],error}
GET              sources/{id}/checks      last 20 checks
GET              sources/{id}/revisions   page_watch observations, each with diff{ops[{op,text}],added_chars,removed_chars}
GET              sources/{id}/changes     sitemap: {total,new,updated,changes[{url,lastmod,first_seen,changed_at,change}]}
GET              sources/{id}/watcher     {config,fields[],revisions[{id,created_at,config,reason,item_count,sample}]}
POST             sources/{id}/watcher/test        {config} → {config,ok,count,sample,error} (dry run, nothing saved)
PUT              sources/{id}/watcher             {config,reason?,force?} → {saved,test,source?}; a failed live test is not saved unless forced
POST             sources/{id}/watcher/rollback/{rev}  re-applies an earlier rule set, with a fresh test
POST             sources/{id}/repair      {health,current,proposals[{config,…test,comparison}],alternates[],steps[]} (applies nothing)

GET/POST         curators             Curator
GET/PATCH/DELETE curators/{id}        detail adds {runs:[Run], recent:[Item]}
POST             curators/{id}/run|pause|resume|duplicate
GET              curators/templates   [{key,name,description,icon,kind,queries,keywords,provider_keys,content_types,interval_hours}]
POST             curators/draft       {goal} → Curator-shaped proposal (AI if configured, else heuristic)
GET              curators/{id}/export JSON config

Curator = {id,name,description,icon,color,kind:'watch'|'research',instructions,queries[],keywords[],exclusions[],
           content_types[],source_ids[],interval_hours(0=manual),enabled,ai_enabled,min_score,max_per_run,
           collection_id,topic_id,alert_enabled,alert_keywords[],alert_min_severity,
           subject,subject_type,aliases[],report,report_at,
           next_run,last_run,last_success,last_status,error,discovery_count,created_at}
Run = {id,started_at,finished_at,status,trigger,new_items,seen_items,errors:[{source,message}]}

POST curators/{id}/report             research: regenerate cited report → Curator

GET/POST         topics               {id,name,description,keywords[],notes,kind:'knowledge'|'news',muted,pinned,count}
GET/PATCH/DELETE topics/{id}          detail adds {items:[Item], recent:[Item], curators:[{id,name}], related:[Topic]}
POST/DELETE      topics/{id}/items/{item_id}

GET/POST         briefings            {id,name,curator_ids[],topic_ids[],frequency:'manual'|'daily'|'weekly',length:'concise'|'detailed',last_generated,next_run}
PATCH/DELETE     briefings/{id}
POST             briefings/{id}/generate → Issue
GET              briefings/{id}/issues   [Issue]   Issue={id,created_at,content,item_ids[],ai}

GET  alerts?state=new|read|resolved|all   items + {severity,state,title,reason,curator_id,item:Item,created_at}
POST alerts/{id}/state                    {state:'read'|'resolved'|'new', snooze_hours?}

GET  search?q&type&provider&scope=all|library|knowledge&offset&limit   local FTS → {items,total,videos:[{id,title,channel_name,thumbnail}],curators,collections,topics}
POST search/web                       {q, providers[]} live → {results:[{title,url,snippet,provider,type}], errors:[{provider,message}]}
GET/POST/DELETE saved-searches        {id,name,query,filters,pinned}

GET  memes/items?view=examples|saved|history&q&community&provider&language&topic&format_id&since&until&offset&limit
                                        meme Items, each + meme:{media_sha,has_media,dup_of,format_id,format_name,community,handle,language,sensitive,spoiler}
GET  memes/facets                       {communities:[{key,count}], languages:[{key,count}]}
GET  memes/trends?state=a,b&include_hidden  {trends:[{id,target_kind:format|community,target_key,name,kind,state,prev_state,
                                        first_observed,last_observed,observations:[{day,count}],metrics{last7,prev7,baseline_week,
                                        samples,communities,sparse},evidence[],updated_at,example_ids,featured_item_id,
                                        exemplar_item_id,followed,hidden}]}   states counted from the profile's own examples
POST memes/trends/{id}/state            {followed?,hidden?} → {followed,hidden}
GET  memes/formats?q&sort=new|active&offset&limit   {formats:[{id,name,kind,example_count,community_count,first_seen,last_seen}],total}
GET  memes/formats/{id}                 format + {items:[Item]}
GET  memes/items/{id}/similar           {items:[Item]}  near-duplicates by perceptual hash
POST memes/items/{id}/explain           {refresh?} → {ai,explanation:{what_shows,why_funny,referencing,origin,why_now,uncertain,ai,at}}
                                        needs AI; stored in enrichment.meme_explain, never overwrites source facts
POST memes/sweep                        analyze pending examples + recount trends now
```


### Meme video capture and Mastodon sources

Meme Tracker accepts a public Mastodon instance or a scoped hashtag URL such as
`https://mastodon.world/tags/memes`. A hashtag URL stays scoped to that hashtag;
an instance URL uses curator queries as hashtag candidates. Only public original
posts are collected. Instances that require timeline authentication report a source error.

Video and GIFV attachments retain a separate poster and video URL. Local capture downloads
MP4/WebM clips up to 25 MB and three minutes through the public HTTPS fetcher, validates a
video stream and decoded frame, and counts them against the existing local asset storage cap.
The Meme Radar drawer loads a captured clip on demand with native playback and seeking.
Pending/failed clips offer capture/retry and the original post. Opening a pending clip refreshes
capture state automatically. Local assets remain profile-authorized; video bytes support GET,
HEAD and single byte-range requests at `/api/hub/items/{id}/assets/video`.
