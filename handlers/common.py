import html
import logging
import re
from typing import Any, Dict, Optional

from aiogram import Bot, types
from aiogram.types import InlineKeyboardMarkup

import config

logger = logging.getLogger(__name__)

MOVIE_CODE_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def is_admin(user_id: int) -> bool:
    return config.ADMIN_ID is not None and user_id == config.ADMIN_ID


async def get_bot_username(bot: Bot) -> str:
    try:
        me = await bot.me()
        return me.username or ""
    except Exception:
        return ""


def build_movie_caption(
    movie_code: str,
    raw_caption: str = "",
    bot_username: str = "",
    total_episodes: int = 1,
    episode_number: int = 1,
) -> str:
    """Build a single clean HTML caption attached directly to the movie video bubble."""
    lines = []

    cleaned = (raw_caption or "").strip()
    if cleaned and cleaned.upper() != movie_code.upper():
        desc = cleaned[:750] + ("..." if len(cleaned) > 750 else "")
        lines.append(html.escape(desc))
        lines.append("")

    if total_episodes > 1:
        lines.append(
            f"📺 <b>Serial kodi:</b> <code>{html.escape(movie_code)}</code> — "
            f"<b>{episode_number}-qism</b> (jami {total_episodes} ta)"
        )
    else:
        lines.append(f"🎬 <b>Kino kodi:</b> <code>{html.escape(movie_code)}</code>")

    bot_tag = f"@{bot_username}" if bot_username else "Bot"
    lines.append("")
    lines.append(f"🤖 {bot_tag} — Hech qanday majburiy obunalarsiz kino bot!")
    return "\n".join(lines)


def build_downloaded_caption(
    title: str = "",
    bot_username: str = "",
    media_format: str = "mp4",
) -> str:
    """Build the sleek single-bubble branded caption for downloaded videos/audio."""
    bot_tag = f"@{bot_username}" if bot_username else "Bot"
    icon = "🎵" if media_format == "mp3" else "🎬"
    lines = []
    cleaned_title = (title or "").strip()
    if cleaned_title and cleaned_title.lower() != "media":
        lines.append(f"{icon} <b>{html.escape(cleaned_title[:120])}</b>")
        lines.append("")
    lines.append(f"🤖 {bot_tag} orqali yuklab olindi.")
    lines.append("🚀 Hech qanday majburiy obunalarsiz bot!")
    return "\n".join(lines)


async def send_movie_to_user(
    bot: Bot,
    message: types.Message,
    movie: Dict[str, Any],
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """
    Send a movie in ONE single message bubble (video + caption + optional buttons)
    via file_id, falling back to copy_message from source channel if needed.
    """
    file_id = movie.get("file_id")
    final_caption = caption if caption is not None else (movie.get("caption") or None)
    media_type = movie.get("media_type") or "video"
    source_chat_id = movie.get("source_chat_id")
    source_message_id = movie.get("source_message_id")

    if file_id:
        try:
            if media_type == "document":
                await message.answer_document(
                    document=file_id,
                    caption=final_caption,
                    parse_mode=parse_mode if final_caption else None,
                    reply_markup=reply_markup,
                )
            else:
                await message.answer_video(
                    video=file_id,
                    caption=final_caption,
                    parse_mode=parse_mode if final_caption else None,
                    supports_streaming=True,
                    reply_markup=reply_markup,
                )
            return True
        except Exception as exc:
            logger.warning(
                "Sending movie '%s' by file_id failed (%s); trying copy_message fallback.",
                movie.get("movie_code"),
                exc,
            )

    if source_chat_id and source_message_id:
        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                caption=final_caption,
                parse_mode=parse_mode if final_caption else None,
                reply_markup=reply_markup,
            )
            return True
        except Exception as exc:
            logger.error(
                "copy_message fallback failed for movie '%s': %s",
                movie.get("movie_code"),
                exc,
            )

    return False
