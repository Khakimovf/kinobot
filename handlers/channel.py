import logging

from aiogram import Bot, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import config
import database as db
from handlers.common import MOVIE_CODE_REGEX

logger = logging.getLogger(__name__)
router = Router(name="channel")


def _extract_video_media(message: types.Message):
    """Return (file_id, media_type) if message contains a video or video document (uploaded or forwarded)."""
    if message.video:
        return message.video.file_id, "video"
    if (
        message.document
        and message.document.mime_type
        and message.document.mime_type.startswith("video/")
    ):
        return message.document.file_id, "document"
    return None, None


@router.channel_post()
async def handle_channel_post(message: types.Message, bot: Bot) -> None:
    """
    Silent channel intake supporting ALL 3 workflows:
    1) Single movie: post/forward 1 video -> send code (e.g. `199` or `A12`).
    2) Multi-episode series: post/forward 2, 3, 4... videos in order -> send code (e.g. `200`).
       Adding more episodes later: post new episode(s) -> send `+200` or `200+`.
    3) Reply to ANY video in the channel (`belgilab`) -> send code (e.g. `199` or `+200`).
    Bot NEVER replies inside the channel (zero spam); logs + optional private DM to ADMIN_ID.
    """
    if config.CHANNEL_ID is None or message.chat.id != config.CHANNEL_ID:
        logger.warning(
            "Ignoring channel_post from chat_id=%s (%s) — configured CHANNEL_ID=%s",
            message.chat.id,
            message.chat.title,
            config.CHANNEL_ID,
        )
        return

    file_id, media_type = _extract_video_media(message)
    if file_id and media_type:
        caption = (message.caption or "").strip()
        queue_len = await db.append_pending_channel_video(
            chat_id=message.chat.id,
            message_id=message.message_id,
            file_id=file_id,
            caption=caption,
            media_type=media_type,
        )
        logger.info(
            "Channel %s: queued pending %s #%d (msg_id=%s) awaiting movie code.",
            message.chat.id,
            media_type,
            queue_len,
            message.message_id,
        )
        return

    if not message.text:
        return

    raw_text = message.text.strip()
    append_mode = False
    if raw_text.startswith("+") and len(raw_text) > 1:
        append_mode = True
        raw_code = raw_text[1:].strip()
    elif raw_text.endswith("+") and len(raw_text) > 1:
        append_mode = True
        raw_code = raw_text[:-1].strip()
    else:
        raw_code = raw_text

    if not MOVIE_CODE_REGEX.fullmatch(raw_code):
        logger.debug(
            "Channel %s: ignoring non-code text message (msg_id=%s).",
            message.chat.id,
            message.message_id,
        )
        return

    episodes_payload = []
    pending_videos = await db.get_pending_channel_videos(message.chat.id)

    # Case 1: Multiple videos were posted/forwarded in sequence (a series!)
    # Save ALL queued videos under this bottom code — even if admin replied to the last episode!
    if len(pending_videos) >= 2 and (
        not message.reply_to_message
        or message.reply_to_message.message_id == pending_videos[-1]["message_id"]
    ):
        episodes_payload = [
            {
                "file_id": item["file_id"],
                "caption": item["caption"],
                "source_chat_id": item["chat_id"],
                "source_message_id": item["message_id"],
                "media_type": item["media_type"],
            }
            for item in pending_videos
        ]
    # Case 2: Admin explicitly replied (`belgilab`) to a specific video (e.g. an older video)
    elif message.reply_to_message:
        rep_fid, rep_type = _extract_video_media(message.reply_to_message)
        if rep_fid and rep_type:
            episodes_payload = [
                {
                    "file_id": rep_fid,
                    "caption": (message.reply_to_message.caption or "").strip(),
                    "source_chat_id": message.chat.id,
                    "source_message_id": message.reply_to_message.message_id,
                    "media_type": rep_type,
                }
            ]

    # Case 3: Single queued video posted right before the code
    if not episodes_payload:
        if not pending_videos:
            logger.debug(
                "Channel %s: received code '%s' but no replied or pending videos found.",
                message.chat.id,
                raw_code,
            )
            return
        episodes_payload = [
            {
                "file_id": item["file_id"],
                "caption": item["caption"],
                "source_chat_id": item["chat_id"],
                "source_message_id": item["message_id"],
                "media_type": item["media_type"],
            }
            for item in pending_videos
        ]

    saved_code = await db.save_movie_episodes(
        movie_code=raw_code,
        episodes=episodes_payload,
        append=append_mode,
    )
    await db.clear_pending_channel_videos(message.chat.id)

    all_episodes = await db.get_movie_episodes(saved_code)
    total_parts = len(all_episodes)
    added_parts = len(episodes_payload)

    logger.info(
        "Channel %s: saved code '%s' (%d video(s) added, %d total episode(s), append=%s).",
        message.chat.id,
        saved_code,
        added_parts,
        total_parts,
        append_mode,
    )

    if config.NOTIFY_ADMIN_ON_CHANNEL_SAVE and config.ADMIN_ID:
        try:
            if total_parts > 1:
                action_label = (
                    f"+{added_parts} ta yangi qism qo'shildi (jami {total_parts} ta qism)"
                    if append_mode
                    else f"yangi serial saqlandi ({total_parts} ta qism)"
                )
                msg_text = (
                    f"✅ Kanaldan {action_label}!\n"
                    f"📺 Kod: <code>{saved_code}</code>"
                )
            else:
                msg_text = (
                    f"✅ Kanaldan yangi kino avtomatik saqlandi!\n"
                    f"🎬 Kod: <code>{saved_code}</code>\n"
                    f"🆔 Video xabar ID: <code>{episodes_payload[0]['source_message_id']}</code>"
                )
            await bot.send_message(
                chat_id=config.ADMIN_ID,
                text=msg_text,
                parse_mode="HTML",
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("Could not DM admin about saved channel movie: %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error notifying admin: %s", exc)
