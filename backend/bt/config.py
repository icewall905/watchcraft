"""Environment config and default runtime settings."""
import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("BT_DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bt.sqlite3"
COOKIES_PATH = DATA_DIR / "cookies.txt"
MEDIA_DIR = Path(os.environ.get("BT_MEDIA_DIR", DATA_DIR / "media")).resolve()
STATIC_DIR = Path(os.environ.get("BT_STATIC_DIR", Path(__file__).parent / "static"))

PASSWORD = os.environ.get("BT_PASSWORD", "")


def _secret() -> str:
    if s := os.environ.get("BT_SECRET"):
        return s
    f = DATA_DIR / ".secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


SECRET = _secret()
POT_URL = os.environ.get("BT_POT_URL", "http://pot:4416")

# Global settings (key -> default). Stored in Setting table, merged over these.
DEFAULT_SETTINGS: dict = {
    # Older databases remain ready; init_db opts new installations into the guide.
    "setup": {"completed": True},
    # Hub web search: "none" | "searxng" (self-hosted, trusted URL) | "brave" (API key).
    "search": {
        "provider": os.environ.get("WC_SEARCH_PROVIDER", "none"),
        "searxng_url": os.environ.get("WC_SEARXNG_URL", ""),
        "brave_api_key": os.environ.get("WC_BRAVE_API_KEY", ""),
        "hub_ai": True,  # use the configured LLM for curator relevance, drafts, reports and briefings
    },
    "llm": {
        "base_url": os.environ.get("BT_LLM_BASE_URL", "http://10.0.10.23:8080/v1"),
        "model": os.environ.get("BT_LLM_MODEL", "currentmodel"),
        "api_key": os.environ.get("BT_LLM_API_KEY", ""),
        "timeout_s": 180,
        "thinking": True,  # False sends chat_template_kwargs.enable_thinking=false
        "max_retries": 2,
        "vision": True,
        "temperature": 0.4,
    },
    "throttle": {
        "min_delay_s": 20,
        "max_delay_s": 60,
        "max_per_hour": 120,
        "max_per_day": 1200,
        "backoff_min_s": 3600,
        "backoff_max_s": 21600,
        "pause_while_playing": True,
        "quiet_hours": "",  # e.g. "01:00-07:00"
        "search_results": 15,
        "max_comments": 40,
        "transcript_max_chars": 40000,
        "proxy": "",  # route YouTube traffic through this proxy, e.g. socks5://user:pass@host:1080
    },
    "agent": {"enabled": True},
    # Hub agents (curators, Source Scout, briefings) allowed to work at the same time.
    "team": {"max_parallel": 1},
    "appearance": {"theme": "projector"},
    "memory": {"enabled": True, "time": "03:00", "timezone": "Europe/Copenhagen", "max_core_tokens": 15000},
    "player": {
        "max_height": 1080,
        "prefer_codec": "avc1",  # avc1 | vp9 | av01
        "autoplay_next": True,
        "default_speed": 1.0,
        "captions_lang": "",
    },
    "sponsorblock": {
        "enabled": True,
        # action per category: skip | ask | show | off
        "categories": {
            "sponsor": "skip", "selfpromo": "skip", "interaction": "skip",
            "intro": "ask", "outro": "ask", "preview": "show",
            "filler": "show", "music_offtopic": "skip", "poi_highlight": "show",
        },
        "min_segment_s": 1.0,
    },
    "extras": {"dearrow": False, "dearrow_thumbnails": False, "ryd": True, "hide_shorts": True},
    "cache": {
        "enabled": True,
        "auto": "accepted",  # accepted | manual
        "max_gb": 500,
        "max_height": 1080,
        "rate_limit": 8_000_000,  # bytes/s
        "min_gap_s": 30,
        "hw_transcode": False,  # re-encode new downloads with NVIDIA NVENC (Settings → Storage toggle)
    },
}
