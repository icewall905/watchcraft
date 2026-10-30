# WatchCraft

**Let curiosity run on autopilot.**

Evolving from BetterTube into a self-hosted personal discovery hub. This first increment preserves video playback and adds RSS/Atom and GitHub release curators at **Discover**. See [architecture, deployment, migration and remaining work](docs/WATCHCRAFT.md).

WatchCraft is a self-hosted YouTube front end for watching and discovering videos on your own terms. Run it on a computer at home, open it in a browser on your devices, and give each family member a place for their own interests. It plays videos without embedding the YouTube player and can use an AI curator to build playlists around the things you actually want to watch.

**[Deploy at home](#deploy-at-home)** · **[How it works](#how-it-works)** · **[Manage your installation](#manage-your-installation)** · **[Development](#development)**

## What you can do

- **Watch without player ads.** WatchCraft resolves and plays video streams through its own player. SponsorBlock can skip or ask about sponsored segments; Return YouTube Dislike and DeArrow are optional community integrations.
- **Make playlists around your interests.** Describe a subject such as “science for kids” or “woodworking”, and the background curator searches for videos that fit. It can consider metadata, transcripts, comments and thumbnails when available.
- **Teach the curator your preferences.** Give feedback and review or edit the lessons it learns. Curated picks are paced to provide variety across topics and creators.
- **Give everyone their own space.** Profiles keep playlists, subscriptions, viewing history and AI memory separate, while server settings are shared. Add an optional PIN to any profile.
- **Watch on a TV or projector.** The interface supports keyboard and remote navigation, an on-screen keyboard and installation as a progressive web app. It works in a regular browser too.
- **Save videos for later.** Download videos to your own storage and adjust caching and storage limits in Settings.
- **Choose your AI provider.** Connect a compatible model endpoint in the web UI, including one hosted on your own network. The AI curator is optional for browsing and playback.

## How it works

WatchCraft runs its web app and background services on your computer using Docker. Your profiles, settings, history and saved videos live in the storage folders you choose. The app contacts YouTube to find and play videos, and optional integrations or your configured AI provider for their respective features. Availability can change when external services change; some videos may require cookies or be unavailable.

To get started, install Docker, run the setup file for your operating system, then open WatchCraft in your browser. Configure your AI connection in **Settings → AI** and create a playlist with a description of what you enjoy. The curator works in the background, and you can pause it or adjust its behavior in Settings.

## Deploy at home

You need a Windows, macOS or Linux computer with Docker, an internet connection, and enough free disk space for the videos you want to keep. A GPU is optional. The AI curator uses a model you run separately or a hosted API; setup does not download a model.

### 1. Install Docker

Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows or macOS. On Windows, use Linux containers with the WSL 2 backend. On Linux, install [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/) and make sure your user can run Docker commands. Setup requires Docker Compose 2.20 or newer and explains if Docker is missing or stopped.

### 2. Download WatchCraft

Choose **Code → Download ZIP** on GitHub, extract the ZIP, and keep the folder somewhere permanent. Alternatively, clone the repository with Git. The setup files must stay alongside the other repository files.

### 3. Run setup

| Your computer | What to run |
| --- | --- |
| Windows | Double-click `setup.bat`. |
| macOS | Double-click `setup.command`. If macOS blocks the downloaded file, open Terminal, type `bash `, drag `setup.sh` into the window, and press Enter. |
| Linux | Open a terminal in the WatchCraft folder and run `bash setup.sh`. |

Setup asks for an app-data folder, a downloaded-video folder and a web port. Press Enter to accept a default. Shared app login is **off by default** for home use. If you choose to enable it, setup asks for a password; pressing Enter at the password prompt generates one for you. Use separate, dedicated folders for app data and videos. Paths with spaces are supported, and Docker Desktop must be allowed to access the folders or external drives you choose.

The first build can take several minutes. Setup checks storage access, waits for the web app to become healthy, and opens your browser at the correct address. It preserves your configuration when run again. Keep the computer and Docker running while using WatchCraft; after a reboot, Docker must start for the app to be available.

### 4. Make yourself at home

If you enabled shared login, enter your setup password. On a new installation, the welcome guide helps you name your first profile, test and save an AI connection, and describe your first playlist. You can **skip AI and start browsing**. The curator stays off until you connect AI through the guide or enable it in Settings.

Your AI server must be reachable from the WatchCraft container. On Docker Desktop, use `host.docker.internal` instead of `localhost` when the AI runs on the same computer. On Linux, use an address reachable on your network. Use the existing **Test connection** button to check the connection. Hosted providers may charge for API use.

Most choices are available in the web UI: profiles, playlists, AI, playback, appearance, download limits, cookies and more. Open **Settings → Setup guide** to revisit the welcome steps. Password, host storage locations and the listening port are in `.env` because they configure the deployment itself.

### Optional profile PINs

In **Settings → Profiles**, add or edit a person to set a PIN with 4–8 digits. Leave it empty for a profile that opens immediately. The shared app password and profile PINs are independent; you can use either, both, or neither.

Protected profiles show a lock in the profile picker. Enter the PIN to open or edit them. Switching away locks a protected profile again; **Lock profile** in the picker lets you lock it immediately. To change or remove a PIN, unlock the profile and edit it in Settings. If you forget a PIN, run the maintenance menu on your server, choose **Reset profile PIN**, and select its profile ID. PINs are for household profile access; server settings and activity tools remain shared.

### Watch from other devices

On the server itself, the default address is [http://localhost:6645](http://localhost:6645). On another device on your home network, replace `localhost` with the server computer's LAN IP address, such as `http://192.168.1.50:6645`. Use your chosen port if you changed it. Allow incoming connections on that port in the server's firewall if necessary. If you enabled shared login, keep its password private. For internet access, arrange HTTPS and suitable access controls rather than forwarding this HTTP port directly.

## Manage your installation

Double-click **`manage.bat` on Windows** or **`manage.command` on macOS**, or run **`bash manage.sh` on Linux**, to choose Start, Update, Backup, Restore, Status, Stop or Reset profile PIN. The scripts use Docker; Python, Node.js and Git are not required for a ZIP installation.

### Updates

Make a backup before replacing files. If you cloned with Git, **Update** makes a backup, pulls changes with `git pull --ff-only`, rebuilds and waits for the app to become ready. It stops if you have local source changes.

For a ZIP installation, choose **Backup**, download and extract the newer repository over the existing WatchCraft folder, preserving `.env`, your storage folders and `backups`, then run setup again. **Update** on a ZIP installation rebuilds the files already in the folder; it cannot fetch a new ZIP for you.

### Backups

Choose **Backup** from the maintenance menu. It briefly stops running services to make a consistent copy of the database, profiles, history, settings and signing secret. You can include downloaded videos, which may make the backup much larger. Services that were running are restarted afterward. Archives are saved in `backups/` beside the scripts. They include credentials and cookies if present: keep them private and copy them to another drive for protection against disk failure.

### Restore or move to another computer

Restore uses **empty destination folders** and refuses to overwrite existing data.

1. Download WatchCraft on the destination computer and install Docker.
2. Configure empty data and video folders without starting the app: run `bash setup.sh --configure-only` on macOS/Linux, or `powershell -ExecutionPolicy Bypass -File setup.ps1 -ConfigureOnly` on Windows. Choose whether to enable shared login and which port to use on this computer.
3. Create a `backups` folder beside the scripts and copy your `.tar.gz` archive into it.
4. Open the maintenance menu, choose **Restore**, and enter the archive filename.
5. Run setup normally to start the restored app.

The destination's password, paths and port remain in use. The old `.env` is saved beside the archive as a separate `.configuration.env` file for reference. If you configured additional values through environment variables, copy the ones you still need from that saved file into the destination `.env`. If you did not include downloaded videos, only application data is restored. To restore on an existing server, stop it and use new empty storage folders; keep the old folders until you have verified the restore.

### Troubleshooting

| What you see | What to try |
| --- | --- |
| Docker is missing or unavailable | Install or update Docker, start it, and wait for its engine to finish starting. On Linux, check your user's Docker permissions. |
| Startup fails because the port is used | Pick an unused `BT_PORT` in `.env`, then run setup again. |
| A storage check fails | Choose folders your user can write to and allow them in Docker Desktop's file-sharing settings. |
| Another device cannot open the app | Use the server's LAN address and chosen port; check its firewall and that both devices can reach each other. |
| AI will not connect | Check the address, model and credentials in Settings. `localhost` inside Docker refers to the container. |
| Playback fails or YouTube asks for sign-in | Check **Settings → YouTube cookies**, inspect logs, and update WatchCraft. Some videos or network addresses may be blocked by YouTube. |

For service details, open a terminal in the repository folder:

```bash
docker compose ps                 # service status
docker compose logs --tail 100     # recent errors
docker compose stop               # stop without deleting files
docker compose up -d              # start existing containers again
```

Setup checks container readiness; it does not guarantee that YouTube or your chosen AI service is reachable. New AI picks appear gradually. If startup still fails, include the relevant error in a GitHub issue after removing passwords, API keys and cookies.

### Optional NVIDIA acceleration

With a compatible NVIDIA GPU, drivers and NVIDIA container support, start with `docker compose -f compose.yaml -f compose.nvidia.yaml up -d --build --wait`, then enable hardware transcoding in **Settings → Storage**. Normal setup works without a GPU. When managing this configuration from a terminal, include both Compose files so the GPU override remains applied.

## Development

The repository contains the FastAPI backend and workers in `backend/bt/`, the SvelteKit interface in `frontend/`, optional projector integration in `host/`, and design notes in `docs/`.

```bash
cd backend && uv venv -p 3.12 .venv && uv pip install -p .venv -r requirements.txt
BT_DATA_DIR=/tmp/bt-dev .venv/bin/uvicorn bt.main:app --port 6699 --reload
BT_DATA_DIR=/tmp/bt-dev .venv/bin/python -m bt.agent
.venv/bin/pytest            # BT_LIVE=1 for network tests
cd frontend && npm ci && npm run dev
```

## Projector
`host/install-desktop.sh` (as root on the projector host) installs `/home/console/bin/bt-mode`
and binds it to remote button 6 (Ctrl+Shift+Y via input-remapper → xbindkeys).

## License

WatchCraft is available under the [MIT License](LICENSE).
