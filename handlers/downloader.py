import asyncio
import logging
from typing import Awaitable, Callable, Optional

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import StateFilter
from aiogram.fsm.state import default_state

import database as db
from api_widgets import (
    TelegramRawAPIError,
    answer_widget_query,
    send_download_widget,
    update_widget_state,
    upload_media_fast,
)
from downloader import (
    DownloadError,
    FileTooLargeError,
    detect_platform,
    extract_url,
    media_downloader,
    normalize_url,
)
from handlers.common import build_downloaded_caption, get_bot_username
from keyboards import get_extract_music_keyboard
from states import UserModes

logger = logging.getLogger(__name__)
router = Router(name="downloader")

PLATFORM_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}


def _format_user_error(exc: Exception) -> str:
    """Convert raw yt-dlp / Telegram errors into clear, actionable Uzbek messages."""
    raw = str(exc)
    lower = raw.lower()

    if isinstance(exc, FileTooLargeError) or any(
        k in lower for k in ("max-filesize", "max_filesize", "too big", "file is too big")
    ):
        return "❌ Fayl hajmi 50 MB (Telegram limiti) dan katta."

    if isinstance(exc, (TelegramNetworkError, TimeoutError)) or "timeout" in lower:
        return (
            "⏱ Telegram serveriga yuklashda vaqt tugadi (timeout).\n"
            "Internet tezligi past yoki fayl hajmi katta — birozdan so'ng qayta urinib ko'ring."
        )

    if any(
        k in lower
        for k in ("private", "login", "sign in", "cookies", "age-restricted", "not available in your country")
    ):
        return (
            "🔒 Video yopiq profil, yosh cheklovi yoki avtorizatsiya talab qiladi "
            "(cookies.txt kerak bo'lishi mumkin)."
        )

    if any(k in lower for k in ("video unavailable", "removed", "deleted", "not found", "404")):
        return "❌ Video o'chirilgan yoki havola noto'g'ri."

    if any(k in lower for k in ("429", "too many requests", "rate limit")):
        return "🚫 Platforma vaqtincha so'rovlarni chekladi. Birozdan so'ng qayta urinib ko'ring."

    short = raw.replace("ERROR:", "").strip().splitlines()[0][:140] if raw else type(exc).__name__
    return f"❌ Yuklashda xatolik: {short}"


async def _send_cached_media(
    bot: Bot,
    chat_id: int,
    media_format: str,
    file_id: str,
    title: str,
    req_id: str,
) -> None:
    bot_username = await get_bot_username(bot)
    caption = build_downloaded_caption(
        title=title, bot_username=bot_username, media_format=media_format
    )
    if media_format == "mp3":
        await bot.send_audio(
            chat_id=chat_id,
            audio=file_id,
            caption=caption,
            parse_mode="HTML",
            title=title or None,
        )
    else:
        await bot.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=caption,
            parse_mode="HTML",
            supports_streaming=True,
            reply_markup=get_extract_music_keyboard(req_id),
        )


def _extract_sent_file_id(sent: types.Message, media_format: str) -> Optional[str]:
    if media_format == "mp3":
        if sent.audio:
            return sent.audio.file_id
        if sent.document:
            return sent.document.file_id
        return None
    if sent.video:
        return sent.video.file_id
    if sent.document:
        return sent.document.file_id
    return None


async def _try_send_direct_url(
    bot: Bot,
    chat_id: int,
    raw_url: str,
    url_key: str,
    media_format: str,
    req_id: str,
) -> bool:
    """
    Fast path (Zero-Download): extract direct media URL via yt-dlp(download=False)
    and ask Telegram servers to fetch it directly in 1-3 seconds.
    Returns True if sent successfully, False to trigger local download fallback.
    """
    direct = await media_downloader.extract_direct_url(raw_url, media_format)
    if not direct:
        return False

    bot_username = await get_bot_username(bot)
    caption = build_downloaded_caption(
        title=direct.title, bot_username=bot_username, media_format=media_format
    )
    try:
        if media_format == "mp3":
            sent = await bot.send_audio(
                chat_id=chat_id,
                audio=direct.direct_url,
                caption=caption,
                parse_mode="HTML",
                title=direct.title or None,
                duration=direct.duration,
                request_timeout=30,
            )
        else:
            sent = await bot.send_video(
                chat_id=chat_id,
                video=direct.direct_url,
                caption=caption,
                parse_mode="HTML",
                duration=direct.duration,
                supports_streaming=True,
                reply_markup=get_extract_music_keyboard(req_id),
                request_timeout=30,
            )

        file_id = _extract_sent_file_id(sent, media_format)
        if file_id:
            await db.save_cached_media(
                url_key=url_key,
                media_format=media_format,
                file_id=file_id,
                title=direct.title,
                duration=direct.duration,
            )
        logger.info("Sent %s via Zero-Download Direct URL in fast mode: %s", media_format, url_key)
        return True
    except Exception as exc:
        logger.info(
            "Direct URL rejected by Telegram for %s (%s); switching to local fallback.",
            url_key,
            exc,
        )
        return False


async def _execute_media_download(
    bot: Bot,
    chat_id: int,
    user_id: int,
    raw_url: str,
    url_key: str,
    media_format: str,
    req_id: str,
    on_status: Callable[[str, str], Awaitable[None]],
    on_finish: Optional[Callable[[bool], Awaitable[None]]] = None,
) -> None:
    """
    Shared download pipeline for both Bot API 10.4 WidgetQuery and
    Bot API 10.3 / classic Inline CallbackQuery:
    1) Zero-Download Direct URL fast path (1-3s)
    2) Local download fallback (progressive <=720p MP4 / fast M4A, 300s upload timeout)
    3) Branded single-bubble caption + 'Videodagi musiqani yuklab olish' button on video
    4) Automatic caching of Telegram file_id + immediate downloads/ cleanup + cooldown
    """
    result = None
    succeeded = False
    try:
        await on_status("loading", "⚡ Tezkor havola tekshirilmoqda...")

        # Step 1: Zero-Download fast path (Telegram fetches media directly)
        if await _try_send_direct_url(bot, chat_id, raw_url, url_key, media_format, req_id):
            succeeded = True
            await on_status("done", "⚡ Tezkor rejimda yuborildi!")
            return

        # Step 2: Local fallback download under Semaphore + asyncio.to_thread
        if media_downloader.is_queue_busy():
            await on_status(
                "queue",
                "⏳ Navbat band, navbatingiz kelishi bilan yuklash boshlanadi...",
            )
        else:
            await on_status("loading", "⬇️ Yuklab olinmoqda, biroz kuting...")

        result = await media_downloader.download(raw_url, media_format)

        size_mb = result.file_size / (1024 * 1024)
        await on_status(
            "uploading",
            f"📤 Telegramga yuborilmoqda ({size_mb:.1f} MB)...",
        )

        bot_username = await get_bot_username(bot)
        caption = build_downloaded_caption(
            title=result.title, bot_username=bot_username, media_format=media_format
        )
        reply_markup = get_extract_music_keyboard(req_id) if media_format == "mp4" else None

        file_id = None
        for attempt in range(2):
            try:
                file_id = await upload_media_fast(
                    bot=bot,
                    chat_id=chat_id,
                    file_path=result.file_path,
                    media_format=media_format,
                    title=result.title,
                    caption=caption,
                    parse_mode="HTML",
                    duration=result.duration,
                    width=result.width,
                    height=result.height,
                    reply_markup=reply_markup,
                    timeout_seconds=300,
                )
                break
            except (TelegramNetworkError, TelegramRawAPIError) as exc:
                if attempt == 0 and (
                    isinstance(exc, TelegramNetworkError)
                    or (isinstance(exc, TelegramRawAPIError) and (exc.error_code or 0) < 400)
                ):
                    logger.warning(
                        "Transient upload error (%s); retrying once...", exc
                    )
                    await asyncio.sleep(1.5)
                    continue
                raise

        if file_id:
            await db.save_cached_media(
                url_key=url_key,
                media_format=media_format,
                file_id=file_id,
                title=result.title,
                duration=result.duration,
            )

        succeeded = True
        await on_status("done", "✅ Muvaffaqiyatli yuklandi va yuborildi!")

    except (
        FileTooLargeError,
        DownloadError,
        TelegramNetworkError,
        TelegramBadRequest,
        TelegramRawAPIError,
    ) as exc:
        logger.error("Media delivery failed for %s (%s): %s", raw_url, media_format, exc)
        await on_status("error", _format_user_error(exc))
    except Exception as exc:
        logger.exception("Unexpected error while downloading %s: %s", raw_url, exc)
        await on_status("error", _format_user_error(exc))
    finally:
        if result is not None:
            media_downloader.cleanup_job(result.job_dir)
        media_downloader.rate_limiter.release(user_id, apply_cooldown=True)
        if on_finish is not None:
            await on_finish(succeeded)


@router.message(
    StateFilter(default_state, UserModes.movie_search, UserModes.media_download),
    F.text.regexp(r"https?://"),
)
async def handle_media_link(message: types.Message, bot: Bot) -> None:
    """Detect http/https links and send an interactive Bot API 10.4 widget (with 10.3 & inline fallback)."""
    user_id = message.from_user.id
    await db.add_user(user_id)

    raw_url = extract_url(message.text or "")
    if not raw_url:
        await message.answer("⚠️ Havolani o'qib bo'lmadi. Iltimos, to'g'ri link yuboring.")
        return

    platform = detect_platform(raw_url)
    if not platform:
        await message.answer(
            "⚠️ Hozircha faqat <b>YouTube</b>, <b>Instagram</b> va <b>TikTok</b> havolalari qo'llab-quvvatlanadi.",
            parse_mode="HTML",
        )
        return

    url_key = normalize_url(raw_url)
    req_id = await db.create_pending_download(
        user_id=user_id,
        raw_url=raw_url,
        url_key=url_key,
    )

    platform_name = PLATFORM_LABELS.get(platform, platform.title())
    await send_download_widget(
        bot=bot,
        chat_id=message.chat.id,
        platform_name=platform_name,
        req_id=req_id,
        is_private_chat=(message.chat.type == "private"),
    )


@router.widget_query(F.data.startswith("dl:"))
async def handle_download_widget_query(query: types.WidgetQuery, bot: Bot) -> None:
    """Handle interactive button clicks from inside Bot API 10.4 HTML/JS widgets."""
    parts = (query.data or "").split(":", 2)
    if len(parts) != 3:
        await answer_widget_query(
            bot, query.id, {"st": "error", "msg": "Noto'g'ri so'rov."}
        )
        return

    _, media_format, req_id = parts
    if media_format not in {"mp4", "mp3"}:
        await answer_widget_query(
            bot, query.id, {"st": "error", "msg": "Noto'g'ri format."}
        )
        return

    user_id = query.from_user.id
    chat_id = query.chat.id if query.chat else user_id
    widget_msg_id = query.message_id

    req = await db.get_pending_download(req_id)
    if not req:
        await answer_widget_query(
            bot,
            query.id,
            {"st": "error", "msg": "Havola muddati tugagan. Linkni qayta yuboring."},
        )
        return

    if req["user_id"] != user_id:
        await answer_widget_query(
            bot,
            query.id,
            {"st": "error", "msg": "Faqat link yuborgan foydalanuvchi yuklay oladi."},
        )
        return

    raw_url = req["raw_url"]
    url_key = req["url_key"]

    cached = await db.get_cached_media(url_key, media_format)
    if cached:
        try:
            await answer_widget_query(
                bot,
                query.id,
                {"st": "cached", "msg": "⚡ Keshdan 1 soniyada yuborildi!"},
            )
            await _send_cached_media(
                bot,
                chat_id=chat_id,
                media_format=media_format,
                file_id=cached["file_id"],
                title=cached["title"],
                req_id=req_id,
            )
            if widget_msg_id:
                await update_widget_state(
                    bot,
                    chat_id,
                    widget_msg_id,
                    {"st": "cached", "msg": "⚡ Keshdan 1 soniyada yuborildi!"},
                )
            return
        except TelegramBadRequest as exc:
            logger.warning(
                "Cached file_id expired for %s (%s): %s", url_key, media_format, exc
            )
            await db.delete_cached_media(url_key, media_format)
        except Exception as exc:
            logger.error("Unexpected error sending cached media from widget: %s", exc)

    allowed, reason, remaining = media_downloader.rate_limiter.check_and_reserve(user_id)
    if not allowed:
        msg = (
            "⏳ Sizda hozir yuklash jarayoni ketmoqda."
            if reason == "active"
            else f"⏳ Anti-spam: qayta yuklash uchun yana {remaining} soniya kuting."
        )
        await answer_widget_query(bot, query.id, {"st": "cooldown", "msg": msg})
        return

    await answer_widget_query(
        bot, query.id, {"st": "loading", "msg": "⚡ Tezkor havola tekshirilmoqda..."}
    )

    async def on_widget_status(state_code: str, msg_text: str) -> None:
        if widget_msg_id:
            await update_widget_state(
                bot,
                chat_id,
                widget_msg_id,
                {"st": state_code, "msg": msg_text},
            )

    await _execute_media_download(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        raw_url=raw_url,
        url_key=url_key,
        media_format=media_format,
        req_id=req_id,
        on_status=on_widget_status,
    )


@router.callback_query(F.data.startswith("dl:"))
async def cb_download_format(callback: types.CallbackQuery, bot: Bot) -> None:
    """Handle clicks from Bot API 10.3 RichMessage buttons or classic InlineKeyboard fallback."""
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    _, media_format, req_id = parts
    if media_format not in {"mp4", "mp3"}:
        await callback.answer("Noto'g'ri format tanlandi.", show_alert=True)
        return

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id

    req = await db.get_pending_download(req_id)
    if not req:
        await callback.answer(
            "⏳ Havola muddati tugagan. Iltimos, linkni qayta yuboring.",
            show_alert=True,
        )
        return

    if req["user_id"] != user_id:
        await callback.answer(
            "Bu tugma faqat link yuborgan foydalanuvchi uchun.", show_alert=True
        )
        return

    raw_url = req["raw_url"]
    url_key = req["url_key"]

    cached = await db.get_cached_media(url_key, media_format)
    if cached:
        try:
            await callback.answer("⚡ Keshdan tezkor yuborilmoqda...")
            await _send_cached_media(
                bot,
                chat_id=chat_id,
                media_format=media_format,
                file_id=cached["file_id"],
                title=cached["title"],
                req_id=req_id,
            )
            return
        except TelegramBadRequest as exc:
            logger.warning(
                "Cached file_id expired for %s (%s): %s", url_key, media_format, exc
            )
            await db.delete_cached_media(url_key, media_format)
        except Exception as exc:
            logger.error("Unexpected error sending cached media: %s", exc)

    allowed, reason, remaining = media_downloader.rate_limiter.check_and_reserve(user_id)
    if not allowed:
        if reason == "active":
            await callback.answer(
                "⏳ Sizda hozir yuklash jarayoni ketmoqda. Tugashini kuting.",
                show_alert=True,
            )
        else:
            await callback.answer(
                f"⏳ Iltimos, qayta yuklash uchun yana {remaining} soniya kuting.",
                show_alert=True,
            )
        return

    await callback.answer()
    status_msg: Optional[types.Message] = None

    async def on_text_status(state_code: str, msg_text: str) -> None:
        nonlocal status_msg
        if state_code == "done":
            return
        if status_msg is None:
            status_msg = await bot.send_message(chat_id=chat_id, text=msg_text)
        else:
            try:
                await status_msg.edit_text(msg_text)
            except TelegramBadRequest:
                pass

    async def on_text_finish(succeeded: bool) -> None:
        if succeeded and status_msg is not None:
            try:
                await status_msg.delete()
            except TelegramBadRequest:
                pass

    await _execute_media_download(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        raw_url=raw_url,
        url_key=url_key,
        media_format=media_format,
        req_id=req_id,
        on_status=on_text_status,
        on_finish=on_text_finish,
    )
