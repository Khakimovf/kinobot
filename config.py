import os
import shutil
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_int(value: Optional[str], default: int, minimum: int = 1) -> int:
    parsed = _parse_optional_int(value)
    if parsed is None or parsed < minimum:
        return default
    return parsed


def _parse_bool(value: Optional[str], default: bool = True) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Core Telegram credentials & IDs
BOT_TOKEN: str = (os.getenv("BOT_TOKEN") or os.getenv("API_TOKEN") or "").strip()
CHANNEL_ID: Optional[int] = _parse_optional_int(
    os.getenv("CHANNEL_ID") or os.getenv("SYNC_CHANNEL_ID")
)
ADMIN_ID: Optional[int] = _parse_optional_int(os.getenv("ADMIN_ID"))

# Storage & paths
DB_NAME: str = (os.getenv("DB_NAME") or "kinobot.db").strip()
DOWNLOADS_DIR: Path = BASE_DIR / "downloads"

_cookies_env = (os.getenv("COOKIES_FILE") or "cookies.txt").strip()
COOKIES_FILE: Path = (
    Path(_cookies_env) if Path(_cookies_env).is_absolute() else BASE_DIR / _cookies_env
)

# Anti-ban, rate-limiting, and upload constraints
DOWNLOAD_COOLDOWN_SECONDS: int = _parse_int(
    os.getenv("DOWNLOAD_COOLDOWN_SECONDS"), default=20, minimum=1
)
MAX_CONCURRENT_DOWNLOADS: int = _parse_int(
    os.getenv("MAX_CONCURRENT_DOWNLOADS"), default=2, minimum=1
)
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB Telegram Bot API limit

FFMPEG_LOCATION: Optional[str] = (os.getenv("FFMPEG_LOCATION") or "").strip() or None
NOTIFY_ADMIN_ON_CHANNEL_SAVE: bool = _parse_bool(
    os.getenv("NOTIFY_ADMIN_ON_CHANNEL_SAVE"), default=True
)

# Bot API 10.5 — setMySettings startup configuration
AUTO_CONFIGURE_BOT_SETTINGS: bool = _parse_bool(
    os.getenv("AUTO_CONFIGURE_BOT_SETTINGS"), default=True
)
BOT_ENABLE_INLINE: bool = _parse_bool(os.getenv("BOT_ENABLE_INLINE"), default=True)
BOT_INLINE_PLACEHOLDER: str = (
    os.getenv("BOT_INLINE_PLACEHOLDER") or "Kino kodini kiriting (masalan: 105)..."
).strip()
BOT_CAN_JOIN_GROUPS: bool = _parse_bool(os.getenv("BOT_CAN_JOIN_GROUPS"), default=True)
BOT_TOPICS_ENABLED: bool = _parse_bool(os.getenv("BOT_TOPICS_ENABLED"), default=False)
BOT_TOPIC_SIDEBAR: bool = _parse_bool(os.getenv("BOT_TOPIC_SIDEBAR"), default=False)


def get_ffmpeg_location() -> Optional[str]:
    """Return configured ffmpeg location, system PATH ffmpeg, or bundled imageio-ffmpeg binary."""
    if FFMPEG_LOCATION and Path(FFMPEG_LOCATION).exists():
        return FFMPEG_LOCATION
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return bundled
    except Exception:
        pass
    return None
