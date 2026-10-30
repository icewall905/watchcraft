# WatchCraft: first development increment

WatchCraft evolves BetterTube into a personal discovery hub. The existing home route remains the TV experience. Discover (`/discover`) adds a responsive, profile-owned article/release feed, curator creation, pause/resume, manual scheduling and saved library. Interests are stored notes; new non-video curators do not yet use AI filtering. Existing video AI remains unchanged.

## Architecture and storage

FastAPI serves the SvelteKit static app, SQLite/SQLModel store application data, and existing standalone workers handle video AI and downloads. The API lifespan now starts a lightweight non-video scheduler. Durable curator records survive restarts; due jobs are checked every 30 seconds. Conditional database claims prevent overlapping runs across processes, with a ten-minute recovery lease. Failures are isolated and retried on the next scheduled interval. Run now queues work for the next poll. No provider credentials are required.

Schema version 1 is additive: `curator`, `discovery`, and the `watchcraft_schema_version` setting. Existing video categories, profile IDs, history, cookies, secrets and BT_* configuration remain intact. Discoveries are deduplicated by canonical HTTPS URL within each profile. Save state is profile-owned. Original source titles are displayed without generated summaries. No external HTML is rendered.

Limitations: content and profile state are currently stored together; only the first discovering curator is retained. Run history, canonical cross-profile content records, editing/deletion, keyword filtering, AI enrichment, briefs, alerts, child-specific restrictions, exports, advanced schedules, pagination and notification rules remain planned. Do not enable these new curators for restricted child profiles until restriction enforcement is implemented. Device mode selection and a dedicated /tv entry point are still planned; existing TV launch URLs remain valid.

## Sources and security

RSS and Atom: use a public HTTPS feed URL. GitHub: use `https://github.com/OWNER/REPO`; the adapter collects up to 20 public releases using GitHub's API. RSS collects at most 50 entries per cycle. Feeds with no new entries produce an empty feed rather than fabricated discoveries. Add future providers behind `discover`, retaining bounded fetches and normalized source URLs.

Requests require HTTPS port 443 and publicly routable DNS addresses. Connections pin the validated IP while verifying TLS against the original host. Each redirect is revalidated. Fetches have 15-second socket timeouts, a 2 MB body limit and XML/JSON type checks. XML DTDs/entities are rejected. Use network egress restrictions as additional protection. Instructions and retrieved data cannot execute commands. Provider errors shown in the UI omit raw exception text to avoid leaking secrets.

Profile endpoints reuse existing profile PIN authorization. Open household profiles remain accessible to household users by design. This is not a multi-tenant authentication system.

## Deploy on 10.0.10.73

1. Back up the existing installation with its maintenance menu before pointing this version at existing data. Keep the archive and original checkout for rollback.
2. For a parallel installation, use `docker compose -p watchcraft -f compose.yaml -f compose.watchcraft.yaml up -d --build --wait` (Compose 2.24.4+ for `!override`). The included override assigns distinct container names to **all four services** and a distinct image tag for API/workers. Default compose names are deliberately retained for in-place compatibility and would conflict with a running BetterTube stack.
3. Configure `BT_PORT=6646` in `.env` and select dedicated data/media paths for a parallel install. Restore a backup into empty destinations using the existing maintenance workflow if you want a copy of BetterTube data. Never run two stacks against the same SQLite data directory.
4. Run setup or `docker compose up -d --build --wait` for an in-place migration after stopping the previous stack. Internal API port remains 6645; external port is 6646.
5. Install `deploy/traefik-wc.yml` as `/opt/traefik/dynamic/wc.yml` on the proxy at 10.0.10.60. Point wc.tdgs.org and watchcraft.tdgs.org DNS at the proxy. Certificates remain managed by the existing Traefik setup.
6. Verify profile switching, PINs, playback and remote Back behavior before retiring the old installation. For rollback, stop WatchCraft and restore the pre-upgrade backup with the original checkout into empty folders.

DNS, reverse proxy and the running server are not modified by repository changes.

## Next priorities

Add provider fixtures, execution history and explicit cancellation; implement shared content/provenance models and a versioned migration runner with automated backup checks; then add AI relevance scoring and curator editing. Preserve and expand remote-navigation browser tests before changing the television shell. Add security advisories and public article extraction only behind the same network boundary. Build briefings and alerts when source provenance and factual enrichment are established.
