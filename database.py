import secrets
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

import config

DB_NAME = config.DB_NAME


def normalize_movie_code(movie_code: str) -> str:
    return movie_code.strip().upper()


async def init_db() -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        # Existing movies table (kept backward-compatible with previous kinobot.db)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_code TEXT UNIQUE,
                file_id TEXT,
                caption TEXT,
                source_chat_id INTEGER,
                source_message_id INTEGER,
                media_type TEXT DEFAULT 'video'
            )
            """
        )

        # Safe non-destructive schema migration for older kinobot.db files
        async with db.execute("PRAGMA table_info(movies)") as cursor:
            existing_columns = {row[1] for row in await cursor.fetchall()}

        if "source_chat_id" not in existing_columns:
            await db.execute("ALTER TABLE movies ADD COLUMN source_chat_id INTEGER")
        if "source_message_id" not in existing_columns:
            await db.execute("ALTER TABLE movies ADD COLUMN source_message_id INTEGER")
        if "media_type" not in existing_columns:
            await db.execute(
                "ALTER TABLE movies ADD COLUMN media_type TEXT DEFAULT 'video'"
            )

        # Existing users table
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Temporarily stores a queue of videos posted in a channel awaiting their code message
        # Supports both single movies (1 video -> code) and multi-episode series (N videos -> code)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_channel_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                caption TEXT DEFAULT '',
                media_type TEXT DEFAULT 'video',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            )
            """
        )

        # Episodes table for multi-part / series movies (1, 2, 3, 4...)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS movie_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_code TEXT NOT NULL,
                episode_number INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                caption TEXT DEFAULT '',
                source_chat_id INTEGER,
                source_message_id INTEGER,
                media_type TEXT DEFAULT 'video',
                UNIQUE(movie_code, episode_number)
            )
            """
        )

        # Cache of downloaded social media files by (normalized_url, format)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS media_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_key TEXT NOT NULL,
                media_format TEXT NOT NULL,
                file_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                duration INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(url_key, media_format)
            )
            """
        )

        # Maps short callback tokens to full user URLs (avoids Telegram 64-byte callback limit)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_downloads (
                req_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                raw_url TEXT NOT NULL,
                url_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await db.commit()


# --- USERS & STATS ---
async def add_user(user_id: int) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        await db.commit()


async def get_all_user_ids() -> List[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_stats() -> Tuple[int, int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM movies") as cursor:
            movies_count = (await cursor.fetchone())[0]
        return users_count, movies_count


async def get_detailed_stats() -> Dict[str, int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM movies") as cursor:
            movies_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM media_cache") as cursor:
            cache_count = (await cursor.fetchone())[0]
        return {
            "users": users_count,
            "movies": movies_count,
            "cached_media": cache_count,
        }


# --- MOVIES & SERIES EPISODES ---
async def get_all_movies() -> List[str]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT movie_code FROM movies ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def save_movie(
    movie_code: str,
    file_id: str,
    caption: str = "",
    source_chat_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
    media_type: str = "video",
) -> str:
    """Save a single-video movie (also synced into movie_episodes as episode 1)."""
    code = await save_movie_episodes(
        movie_code=movie_code,
        episodes=[
            {
                "file_id": file_id,
                "caption": caption or "",
                "source_chat_id": source_chat_id,
                "source_message_id": source_message_id,
                "media_type": media_type or "video",
            }
        ],
        append=False,
    )
    return code


async def save_movie_episodes(
    movie_code: str,
    episodes: List[Dict[str, Any]],
    append: bool = False,
) -> str:
    """
    Save 1 or more videos (episodes) under a single movie_code.
    If append=True, appends new episodes after existing ones (e.g. adding episode 5 to a 4-part series).
    """
    if not episodes:
        raise ValueError("episodes list cannot be empty")

    code = normalize_movie_code(movie_code)
    first = episodes[0]

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM movies WHERE UPPER(movie_code) = ? AND movie_code != ?",
            (code, code),
        )

        start_num = 1
        if append:
            async with db.execute(
                "SELECT COALESCE(MAX(episode_number), 0) FROM movie_episodes WHERE UPPER(movie_code) = ?",
                (code,),
            ) as cursor:
                max_ep = (await cursor.fetchone())[0]
            # If the movie only existed in legacy `movies` table without movie_episodes rows, migrate it first
            if max_ep == 0:
                async with db.execute(
                    "SELECT file_id, caption, source_chat_id, source_message_id, media_type "
                    "FROM movies WHERE UPPER(movie_code) = ? LIMIT 1",
                    (code,),
                ) as cursor:
                    legacy = await cursor.fetchone()
                if legacy:
                    await db.execute(
                        """
                        INSERT INTO movie_episodes (
                            movie_code, episode_number, file_id, caption,
                            source_chat_id, source_message_id, media_type
                        ) VALUES (?, 1, ?, ?, ?, ?, ?)
                        """,
                        (code, legacy[0], legacy[1] or "", legacy[2], legacy[3], legacy[4] or "video"),
                    )
                    max_ep = 1
            start_num = max_ep + 1
        else:
            await db.execute(
                "DELETE FROM movie_episodes WHERE UPPER(movie_code) = ?", (code,)
            )

        # Ensure summary row exists in `movies` table (keeps stats/list/backward compatibility)
        if not append or start_num == 1:
            await db.execute(
                """
                INSERT INTO movies (
                    movie_code, file_id, caption, source_chat_id, source_message_id, media_type
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(movie_code) DO UPDATE SET
                    file_id = excluded.file_id,
                    caption = excluded.caption,
                    source_chat_id = excluded.source_chat_id,
                    source_message_id = excluded.source_message_id,
                    media_type = excluded.media_type
                """,
                (
                    code,
                    first["file_id"],
                    first.get("caption") or "",
                    first.get("source_chat_id"),
                    first.get("source_message_id"),
                    first.get("media_type") or "video",
                ),
            )

        for idx, ep in enumerate(episodes, start=start_num):
            await db.execute(
                """
                INSERT INTO movie_episodes (
                    movie_code, episode_number, file_id, caption,
                    source_chat_id, source_message_id, media_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(movie_code, episode_number) DO UPDATE SET
                    file_id = excluded.file_id,
                    caption = excluded.caption,
                    source_chat_id = excluded.source_chat_id,
                    source_message_id = excluded.source_message_id,
                    media_type = excluded.media_type
                """,
                (
                    code,
                    idx,
                    ep["file_id"],
                    ep.get("caption") or "",
                    ep.get("source_chat_id"),
                    ep.get("source_message_id"),
                    ep.get("media_type") or "video",
                ),
            )

        await db.commit()
    return code


async def get_movie_episodes(movie_code: str) -> List[Dict[str, Any]]:
    """Return ordered list of episodes for movie_code (falls back to legacy `movies` row)."""
    code = normalize_movie_code(movie_code)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT movie_code, episode_number, file_id, caption,
                   source_chat_id, source_message_id, media_type
            FROM movie_episodes
            WHERE UPPER(movie_code) = ?
            ORDER BY episode_number ASC
            """,
            (code,),
        ) as cursor:
            rows = await cursor.fetchall()

    if rows:
        return [
            {
                "movie_code": row[0],
                "episode_number": row[1],
                "file_id": row[2],
                "caption": row[3] or "",
                "source_chat_id": row[4],
                "source_message_id": row[5],
                "media_type": row[6] or "video",
            }
            for row in rows
        ]

    legacy = await get_movie_full(code)
    if legacy:
        legacy["episode_number"] = 1
        return [legacy]
    return []


async def get_movie_episode(movie_code: str, episode_number: int) -> Optional[Dict[str, Any]]:
    episodes = await get_movie_episodes(movie_code)
    for ep in episodes:
        if ep["episode_number"] == episode_number:
            return ep
    return None


async def get_movie(movie_code: str) -> Optional[Tuple[str, str]]:
    """Backward-compatible lookup returning (file_id, caption)."""
    movie = await get_movie_full(movie_code)
    if not movie:
        return None
    return movie["file_id"], movie["caption"]


async def get_movie_full(movie_code: str) -> Optional[Dict[str, Any]]:
    code = normalize_movie_code(movie_code)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT movie_code, file_id, caption, source_chat_id, source_message_id, media_type
            FROM movies
            WHERE UPPER(movie_code) = ?
            LIMIT 1
            """,
            (code,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "movie_code": row[0],
                "file_id": row[1],
                "caption": row[2] or "",
                "source_chat_id": row[3],
                "source_message_id": row[4],
                "media_type": row[5] or "video",
            }


async def get_random_movie() -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT movie_code, file_id, caption, source_chat_id, source_message_id, media_type
            FROM movies
            ORDER BY RANDOM()
            LIMIT 1
            """
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "movie_code": row[0],
                "file_id": row[1],
                "caption": row[2] or "",
                "source_chat_id": row[3],
                "source_message_id": row[4],
                "media_type": row[5] or "video",
            }


# --- PENDING CHANNEL VIDEOS QUEUE (SINGLE MOVIE & MULTI-EPISODE SERIES INTAKE) ---
async def append_pending_channel_video(
    chat_id: int,
    message_id: int,
    file_id: str,
    caption: str = "",
    media_type: str = "video",
) -> int:
    """Append a channel video (uploaded or forwarded) to the pending queue and return queue length."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO pending_channel_queue (
                chat_id, message_id, file_id, caption, media_type
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                file_id = excluded.file_id,
                caption = excluded.caption,
                media_type = excluded.media_type
            """,
            (chat_id, message_id, file_id, caption or "", media_type),
        )
        await db.commit()
        async with db.execute(
            "SELECT COUNT(*) FROM pending_channel_queue WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            return (await cursor.fetchone())[0]


async def get_pending_channel_videos(chat_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT chat_id, message_id, file_id, caption, media_type
            FROM pending_channel_queue
            WHERE chat_id = ?
            ORDER BY message_id ASC, id ASC
            """,
            (chat_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "chat_id": row[0],
                    "message_id": row[1],
                    "file_id": row[2],
                    "caption": row[3] or "",
                    "media_type": row[4] or "video",
                }
                for row in rows
            ]


async def clear_pending_channel_videos(chat_id: int) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM pending_channel_queue WHERE chat_id = ?", (chat_id,)
        )
        await db.commit()


# Backward-compatible aliases
async def set_pending_channel_video(
    chat_id: int,
    message_id: int,
    file_id: str,
    caption: str = "",
    media_type: str = "video",
) -> None:
    await append_pending_channel_video(chat_id, message_id, file_id, caption, media_type)


async def get_pending_channel_video(chat_id: int) -> Optional[Dict[str, Any]]:
    videos = await get_pending_channel_videos(chat_id)
    return videos[-1] if videos else None


async def clear_pending_channel_video(chat_id: int) -> None:
    await clear_pending_channel_videos(chat_id)


# --- MEDIA CACHE (ANTI-BAN FILE_ID CACHE) ---
async def get_cached_media(url_key: str, media_format: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT file_id, title, duration
            FROM media_cache
            WHERE url_key = ? AND media_format = ?
            LIMIT 1
            """,
            (url_key, media_format),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "file_id": row[0],
                "title": row[1] or "",
                "duration": row[2],
            }


async def save_cached_media(
    url_key: str,
    media_format: str,
    file_id: str,
    title: str = "",
    duration: Optional[int] = None,
) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO media_cache (url_key, media_format, file_id, title, duration)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url_key, media_format) DO UPDATE SET
                file_id = excluded.file_id,
                title = excluded.title,
                duration = excluded.duration,
                created_at = CURRENT_TIMESTAMP
            """,
            (url_key, media_format, file_id, title or "", duration),
        )
        await db.commit()


async def delete_cached_media(url_key: str, media_format: str) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM media_cache WHERE url_key = ? AND media_format = ?",
            (url_key, media_format),
        )
        await db.commit()


# --- PENDING DOWNLOAD REQUESTS (SHORT CALLBACK TOKENS) ---
async def create_pending_download(user_id: int, raw_url: str, url_key: str) -> str:
    req_id = secrets.token_hex(6)
    async with aiosqlite.connect(DB_NAME) as db:
        # Opportunistically clean requests older than 24 hours
        await db.execute(
            "DELETE FROM pending_downloads WHERE created_at < datetime('now', '-1 day')"
        )
        await db.execute(
            """
            INSERT INTO pending_downloads (req_id, user_id, raw_url, url_key)
            VALUES (?, ?, ?, ?)
            """,
            (req_id, user_id, raw_url, url_key),
        )
        await db.commit()
    return req_id


async def get_pending_download(req_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT req_id, user_id, raw_url, url_key
            FROM pending_downloads
            WHERE req_id = ?
            LIMIT 1
            """,
            (req_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "req_id": row[0],
                "user_id": row[1],
                "raw_url": row[2],
                "url_key": row[3],
            }
