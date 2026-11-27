import asyncio
import logging
import math
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import config

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

TRACKING_QUERY_PARAMS = {
    "si",
    "igsh",
    "igshid",
    "feature",
    "pp",
    "t",
    "_r",
    "_t",
    "is_from_webapp",
    "sender_device",
    "sender_web_id",
    "fbclid",
    "gclid",
    "ref",
    "s",
}

IGNORED_TEMP_SUFFIXES = {
    ".part",
    ".ytdl",
    ".tmp",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".json",
}

# Telegram Bot API server-side URL fetch limit is ~20 MB; larger files fall back to local upload (up to 50 MB)
TELEGRAM_DIRECT_URL_MAX_BYTES = 20 * 1024 * 1024


class DownloadError(Exception):
    """Base error for media downloads."""


class FileTooLargeError(DownloadError):
    """Raised when the requested media exceeds Telegram's 50 MB upload limit."""


@dataclass
class DirectMediaInfo:
    direct_url: str
    title: str
    duration: Optional[int]
    file_size: Optional[int]
    ext: str
    media_format: str


@dataclass
class DownloadResult:
    file_path: Path
    job_dir: Path
    title: str
    duration: Optional[int]
    media_format: str
    file_size: int
    ext: str
    width: Optional[int] = None
    height: Optional[int] = None


def extract_url(text: str) -> Optional[str]:
    """Extract the first http/https URL from text."""
    if not text:
        return None
    match = URL_REGEX.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,);!?")


def detect_platform(url: str) -> Optional[str]:
    """Return 'youtube', 'instagram', 'tiktok', or None if unsupported."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None

    if host.startswith("www."):
        host = host[4:]

    if host in {"youtu.be", "youtube.com", "m.youtube.com", "music.youtube.com"} or host.endswith(
        ".youtube.com"
    ):
        return "youtube"
    if host in {"instagram.com", "instagr.am"} or host.endswith(".instagram.com"):
        return "instagram"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    return None


def normalize_url(url: str) -> str:
    """
    Canonicalize supported URLs so tracking parameters and alias domains
    hit the same SQLite cache key.
    """
    parsed = urlparse(url.strip())
    scheme = "https"
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    query_dict = parse_qs(parsed.query, keep_blank_values=False)

    if host == "youtu.be" and path:
        video_id = path.lstrip("/").split("/")[0]
        return f"https://www.youtube.com/watch?v={video_id}"

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if path.startswith("/shorts/"):
            parts = path.split("/")
            if len(parts) >= 3 and parts[2]:
                return f"https://www.youtube.com/watch?v={parts[2]}"
        if path == "/watch" and "v" in query_dict and query_dict["v"]:
            return f"https://www.youtube.com/watch?v={query_dict['v'][0]}"

    if host in {"instagram.com", "instagr.am"}:
        return f"https://www.instagram.com{path}"

    cleaned_params = {
        k: v[0]
        for k, v in sorted(query_dict.items())
        if k.lower() not in TRACKING_QUERY_PARAMS and not k.lower().startswith("utm_")
    }
    clean_query = urlencode(cleaned_params)
    return urlunparse((scheme, host, path, "", clean_query, ""))


class DownloadRateLimiter:
    """Per-user anti-spam cooldown and in-flight download guard."""

    def __init__(self, cooldown_seconds: int) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._last_download: Dict[int, float] = {}
        self._active_users: Set[int] = set()

    def check_and_reserve(self, user_id: int) -> Tuple[bool, str, int]:
        """
        Try to reserve a download slot for user_id.
        Returns (allowed, reason, remaining_seconds).
        reason is '', 'active', or 'cooldown'.
        """
        if user_id in self._active_users:
            return False, "active", 0

        now = time.monotonic()
        last = self._last_download.get(user_id)
        if last is not None:
            elapsed = now - last
            if elapsed < self.cooldown_seconds:
                remaining = math.ceil(self.cooldown_seconds - elapsed)
                return False, "cooldown", remaining

        self._active_users.add(user_id)
        return True, "", 0

    def release(self, user_id: int, apply_cooldown: bool = True) -> None:
        self._active_users.discard(user_id)
        if apply_cooldown:
            self._last_download[user_id] = time.monotonic()


class MediaDownloader:
    """
    Fast asynchronous yt-dlp wrapper:
    1) Zero-download Direct URL extraction (`download=False`) so Telegram fetches directly in 1-2s.
    2) Local download fallback with progressive MP4 priority (<=720p, <45M), fast native M4A audio,
       concurrency Semaphore, cookies.txt, browser User-Agent, and immediate downloads/ cleanup.
    """

    def __init__(
        self,
        downloads_dir: Path = config.DOWNLOADS_DIR,
        max_concurrent: int = config.MAX_CONCURRENT_DOWNLOADS,
        cooldown_seconds: int = config.DOWNLOAD_COOLDOWN_SECONDS,
    ) -> None:
        self.downloads_dir = downloads_dir
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = DownloadRateLimiter(cooldown_seconds)

    def prepare_downloads_dir(self) -> None:
        """Ensure downloads/ exists and purge stale temp directories from prior runs."""
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        for child in self.downloads_dir.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove stale temp item %s: %s", child, exc)

    @staticmethod
    def cleanup_job(job_dir: Path) -> None:
        shutil.rmtree(job_dir, ignore_errors=True)

    def is_queue_busy(self) -> bool:
        return self.semaphore.locked()

    @staticmethod
    def _detect_js_runtimes() -> Dict[str, Dict[str, Any]]:
        runtimes: Dict[str, Dict[str, Any]] = {}
        for rt in ("deno", "node", "bun", "qjs"):
            exe = shutil.which(rt)
            if exe:
                key = "quickjs" if rt == "qjs" else rt
                runtimes[key] = {"path": exe}
        return runtimes

    def _base_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 30,
            "sleep_interval_requests": 1,
            "geo_bypass": True,
            "max_filesize": config.MAX_FILE_SIZE_BYTES,
            "http_headers": {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }

        js_runtimes = self._detect_js_runtimes()
        if js_runtimes:
            opts["js_runtimes"] = js_runtimes

        if config.COOKIES_FILE.is_file() and config.COOKIES_FILE.stat().st_size > 0:
            opts["cookiefile"] = str(config.COOKIES_FILE)
            logger.debug("Using cookies file: %s", config.COOKIES_FILE)

        return opts

    def _build_direct_opts(self, media_format: str) -> Dict[str, Any]:
        """Options for Zero-Download Direct URL extraction (`download=False`)."""
        opts = self._base_ydl_opts()
        if media_format == "mp3":
            opts["format"] = (
                "bestaudio[ext=m4a][filesize<20M]/"
                "bestaudio[ext=m4a][filesize<45M]/"
                "bestaudio[ext=m4a]/bestaudio"
            )
        else:
            # Must be a single pre-merged file (video + audio) so Telegram can fetch it directly
            opts["format"] = (
                "best[ext=mp4][height<=720][filesize<20M]/"
                "best[ext=mp4][height<=720][filesize<45M]/"
                "best[ext=mp4][filesize<45M]/best[ext=mp4]"
            )
        return opts

    def _build_ydl_opts(self, job_dir: Path, media_format: str) -> Dict[str, Any]:
        """Options for local fallback download (prioritizes fast progressive MP4 & native M4A)."""
        ffmpeg_loc = config.get_ffmpeg_location()
        opts = self._base_ydl_opts()
        opts["outtmpl"] = str(job_dir / "%(id).60s.%(ext)s")

        if ffmpeg_loc:
            opts["ffmpeg_location"] = ffmpeg_loc

        if media_format == "mp3":
            # Fast native M4A audio (no slow MP3 re-encoding; plays natively in Telegram audio player)
            opts["format"] = (
                "bestaudio[ext=m4a][filesize<15M]/"
                "bestaudio[ext=m4a][filesize<45M]/"
                "bestaudio[ext=m4a][filesize_approx<45M]/"
                "bestaudio[ext=m4a]/bestaudio[filesize<45M]/bestaudio"
            )
        else:
            # 1) Prefer ready pre-merged MP4 (<=720p, <20M) — zero ffmpeg merge & fast upload!
            # 2) Then compact <=20M H.264+M4A (720p for short videos, 360p/240p for 10+ min videos)
            # 3) Fallback up to 45M only if no smaller stream exists
            if ffmpeg_loc:
                opts["format"] = (
                    "best[ext=mp4][height<=720][filesize<20M]/"
                    "bestvideo[ext=mp4][height<=720][vcodec^=avc1][filesize<16M]+bestaudio[ext=m4a][filesize<5M]/"
                    "bestvideo[ext=mp4][height<=480][vcodec^=avc1][filesize<18M]+bestaudio[ext=m4a][filesize<5M]/"
                    "bestvideo[ext=mp4][height<=360][vcodec^=avc1][filesize<22M]+bestaudio[ext=m4a][filesize<5M]/"
                    "best[ext=mp4][height<=720][filesize<45M]/"
                    "best[ext=mp4][filesize<45M]/"
                    "bestvideo[ext=mp4][filesize<38M]+bestaudio[ext=m4a][filesize<10M]/"
                    "best[ext=mp4][filesize_approx<45M]/"
                    "best[ext=mp4]/best[filesize<50M]/best"
                )
                opts["merge_output_format"] = "mp4"
            else:
                opts["format"] = (
                    "best[ext=mp4][height<=720][filesize<20M]/"
                    "best[ext=mp4][height<=720][filesize<45M]/"
                    "best[ext=mp4][filesize<45M]/"
                    "best[ext=mp4][filesize_approx<45M]/"
                    "best[ext=mp4]/best[filesize<50M]/best"
                )

        return opts

    def _extract_direct_sync(
        self, url: str, media_format: str
    ) -> Optional[DirectMediaInfo]:
        import yt_dlp

        # YouTube googlevideo.com URLs are signed with &ip=<host_ip>, so Telegram servers
        # receive HTTP 403 when fetching them directly; skip straight to local relay to save 3-5s.
        if detect_platform(url) == "youtube":
            return None

        ydl_opts = self._build_direct_opts(media_format)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            logger.debug("Direct URL extraction skipped for %s: %s", url, exc)
            return None

        if not info:
            return None

        if "entries" in info and info["entries"]:
            info = next((e for e in info["entries"] if e), info)

        # If yt-dlp selected split streams (requested_formats), Telegram cannot fetch a single URL
        if info.get("requested_formats"):
            return None

        direct_url = info.get("url")
        protocol = (info.get("protocol") or "").lower()
        if not direct_url or not direct_url.startswith(("http://", "https://")):
            return None
        if protocol and protocol not in {"http", "https"}:
            return None

        # Ensure video mode didn't accidentally match a video-only or audio-only stream
        if media_format == "mp4":
            if info.get("acodec") == "none" or info.get("vcodec") == "none":
                return None

        file_size = info.get("filesize") or info.get("filesize_approx")
        if isinstance(file_size, (int, float)) and file_size > TELEGRAM_DIRECT_URL_MAX_BYTES:
            logger.debug(
                "Direct URL size %.1f MB exceeds Telegram 20 MB URL fetch limit; using local fallback.",
                file_size / (1024 * 1024),
            )
            return None

        title = (info.get("title") or "Media").strip()
        duration_raw = info.get("duration")
        duration = int(duration_raw) if isinstance(duration_raw, (int, float)) else None

        return DirectMediaInfo(
            direct_url=direct_url,
            title=title[:120],
            duration=duration,
            file_size=int(file_size) if isinstance(file_size, (int, float)) else None,
            ext=(info.get("ext") or ("m4a" if media_format == "mp3" else "mp4")).lower(),
            media_format=media_format,
        )

    async def extract_direct_url(
        self, url: str, media_format: str
    ) -> Optional[DirectMediaInfo]:
        """Try to obtain a direct single-file HTTP(S) media URL without downloading to disk."""
        return await asyncio.to_thread(self._extract_direct_sync, url, media_format)

    def _download_sync(
        self, url: str, media_format: str, job_dir: Path
    ) -> DownloadResult:
        import yt_dlp

        job_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts = self._build_ydl_opts(job_dir, media_format)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            err_text = str(exc).lower()
            if "file is larger than max-filesize" in err_text or "max_filesize" in err_text:
                raise FileTooLargeError(
                    "Fayl hajmi Telegram limiti (50 MB) dan oshib ketdi."
                ) from exc
            raise DownloadError(str(exc)) from exc
        except Exception as exc:
            raise DownloadError(str(exc)) from exc

        if not info:
            raise DownloadError("Media ma'lumotlarini olib bo'lmadi.")

        if "entries" in info and info["entries"]:
            info = next((e for e in info["entries"] if e), info)

        media_files = [
            f
            for f in job_dir.iterdir()
            if f.is_file() and f.suffix.lower() not in IGNORED_TEMP_SUFFIXES
        ]
        if not media_files:
            raise FileTooLargeError(
                "Fayl hajmi 50 MB limitidan katta yoki format topilmadi."
            )

        file_path = max(media_files, key=lambda p: p.stat().st_size)
        file_size = file_path.stat().st_size
        if file_size > config.MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(
                f"Fayl hajmi {file_size / (1024 * 1024):.1f} MB — 50 MB limitidan katta."
            )

        title = (info.get("title") or "Media").strip()
        duration_raw = info.get("duration")
        duration = int(duration_raw) if isinstance(duration_raw, (int, float)) else None
        width_raw = info.get("width")
        height_raw = info.get("height")
        width = int(width_raw) if isinstance(width_raw, (int, float)) and width_raw > 0 else None
        height = int(height_raw) if isinstance(height_raw, (int, float)) and height_raw > 0 else None

        return DownloadResult(
            file_path=file_path,
            job_dir=job_dir,
            title=title[:120],
            duration=duration,
            media_format=media_format,
            file_size=file_size,
            ext=file_path.suffix.lower().lstrip("."),
            width=width,
            height=height,
        )

    async def download(self, url: str, media_format: str) -> DownloadResult:
        """
        Download media asynchronously inside a worker thread under the global
        concurrency semaphore. Caller MUST call cleanup_job(result.job_dir)
        in a finally block after uploading to Telegram.
        """
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        job_dir = self.downloads_dir / uuid.uuid4().hex

        async with self.semaphore:
            try:
                return await asyncio.to_thread(
                    self._download_sync, url, media_format, job_dir
                )
            except Exception:
                self.cleanup_job(job_dir)
                raise


media_downloader = MediaDownloader()
