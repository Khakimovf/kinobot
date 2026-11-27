import asyncio
import logging

from aiogram import Bot, F, Router, types
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

import config
import database as db
from api_widgets import apply_bot_settings, format_bot_settings_panel
from handlers.common import MOVIE_CODE_REGEX, is_admin
from keyboards import get_admin_keyboard, get_main_menu_keyboard
from states import AdminStates

logger = logging.getLogger(__name__)
router = Router(name="admin")


@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🛠 Admin boshqaruv paneli:",
        reply_markup=get_admin_keyboard(),
    )


@router.message(Command("settings"))
async def cmd_bot_settings(message: types.Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    text, kb = await format_bot_settings_panel(bot)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_bot_settings")
async def cb_admin_bot_settings(callback: types.CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
    text, kb = await format_bot_settings_panel(bot)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("botset:"))
async def cb_toggle_bot_setting(callback: types.CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri buyruq.", show_alert=True)
        return

    _, key, raw_val = parts
    enabled = raw_val == "1"

    mapping = {
        "inline": {"supports_inline_queries": enabled},
        "groups": {"can_join_groups": enabled},
        "topics": {"has_topics_enabled": enabled},
        "sidebar": {"has_topic_sidebar": enabled},
    }

    if key == "apply_env":
        ok = await apply_bot_settings(bot)
    elif key in mapping:
        ok = await apply_bot_settings(bot, overrides=mapping[key])
    else:
        await callback.answer("Noma'lum sozlama.", show_alert=True)
        return

    if not ok:
        await callback.answer("❌ Sozlamani yangilab bo'lmadi.", show_alert=True)
        return

    text, kb = await format_bot_settings_panel(bot)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Yangilandi!")


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    await state.clear()
    if current_state:
        await message.answer(
            "❌ Joriy amal bekor qilindi.",
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        await message.answer(
            "Bekor qilinadigan faol amal yo'q.",
            reply_markup=get_main_menu_keyboard(),
        )


async def _run_channel_sync(message: types.Message, bot: Bot) -> None:
    if config.CHANNEL_ID is None or config.ADMIN_ID is None:
        await message.answer(
            "⚠️ `.env` faylida `CHANNEL_ID` va `ADMIN_ID` sozlanmagan."
        )
        return

    await message.answer(
        "🔄 Kanal skaner qilinmoqda, kuting... (Video + kod juftliklari va eski formatlar tekshiriladi)"
    )

    try:
        temp_msg = await bot.send_message(chat_id=config.CHANNEL_ID, text="sync_init")
        latest_msg_id = temp_msg.message_id
        await temp_msg.delete()
    except Exception as exc:
        await message.answer(
            f"❌ Sinxronizatsiyani ishga tushirib bo'lmadi. "
            f"Bot kanalda admin ekanligiga ishonch hosil qiling.\nXato: {exc}"
        )
        return

    await message.answer(
        f"🔍 1 dan {latest_msg_id} gacha bo'lgan xabarlar tekshirilmoqda..."
    )

    success_count = 0
    pending_videos = []

    for msg_id in range(1, latest_msg_id):
        try:
            fwd_msg = await bot.forward_message(
                chat_id=config.ADMIN_ID,
                from_chat_id=config.CHANNEL_ID,
                message_id=msg_id,
            )

            video_file_id = None
            media_type = "video"
            if fwd_msg.video:
                video_file_id = fwd_msg.video.file_id
            elif (
                fwd_msg.document
                and fwd_msg.document.mime_type
                and fwd_msg.document.mime_type.startswith("video/")
            ):
                video_file_id = fwd_msg.document.file_id
                media_type = "document"

            if video_file_id:
                caption = (fwd_msg.caption or "").strip()
                pending_videos.append(
                    {
                        "file_id": video_file_id,
                        "caption": caption,
                        "source_chat_id": config.CHANNEL_ID,
                        "source_message_id": msg_id,
                        "media_type": media_type,
                    }
                )
                # Legacy format: if the video's caption itself is strictly a numeric code
                if caption.isdigit() and len(pending_videos) == 1:
                    await db.save_movie(
                        movie_code=caption,
                        file_id=video_file_id,
                        caption=caption,
                        source_chat_id=config.CHANNEL_ID,
                        source_message_id=msg_id,
                        media_type=media_type,
                    )
                    success_count += 1
                    pending_videos.clear()
            elif fwd_msg.text and pending_videos:
                raw_text = fwd_msg.text.strip()
                append_mode = raw_text.startswith("+") or raw_text.endswith("+")
                code_text = raw_text.strip("+").strip()
                if code_text and MOVIE_CODE_REGEX.fullmatch(code_text):
                    await db.save_movie_episodes(
                        movie_code=code_text,
                        episodes=pending_videos,
                        append=append_mode,
                    )
                    success_count += 1
                    pending_videos.clear()

            await fwd_msg.delete()
            await asyncio.sleep(0.35)

        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except TelegramBadRequest:
            pass
        except TelegramForbiddenError:
            break
        except Exception as exc:
            logger.error("Error syncing msg %s: %s", msg_id, exc)
            await asyncio.sleep(1)

    await message.answer(
        f"✅ Sinxronizatsiya yakunlandi! {success_count} ta kino bazaga saqlandi/yangilandi."
    )


@router.message(Command("sync"))
async def cmd_sync(message: types.Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    await _run_channel_sync(message, bot)


@router.callback_query(F.data == "admin_sync")
async def cb_admin_sync(callback: types.CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
    await callback.answer("Sinxronizatsiya boshlandi...")
    await _run_channel_sync(callback.message, bot)


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return

    stats = await db.get_detailed_stats()
    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👥 Umumiy foydalanuvchilar: <b>{stats['users']}</b> ta\n"
        f"🎬 Bazadagi kinolar: <b>{stats['movies']}</b> ta\n"
        f"⚡ Keshlangan media fayllar: <b>{stats['cached_media']}</b> ta"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_movies_list")
async def cb_admin_movies_list(callback: types.CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return

    movies = await db.get_all_movies()
    if not movies:
        await callback.message.answer("Bazada hozircha kinolar yo'q.")
        await callback.answer()
        return

    text = f"🎬 <b>Kinolar ro'yxati ({len(movies)} ta)</b>\n\n"
    text += ", ".join(movies[:100])
    if len(movies) > 100:
        text += f"\n... va yana {len(movies) - 100} ta qolgan."

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_upload_movie")
async def cb_admin_upload_movie(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return

    await callback.message.answer(
        "📥 Videoni to'g'ridan-to'g'ri menga yuboring (bekor qilish uchun /cancel):"
    )
    await state.set_state(AdminStates.waiting_for_movie_video)
    await callback.answer()


@router.message(AdminStates.waiting_for_movie_video, F.video | F.document)
async def process_admin_video(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    if message.video:
        file_id = message.video.file_id
        media_type = "video"
    elif (
        message.document
        and message.document.mime_type
        and message.document.mime_type.startswith("video/")
    ):
        file_id = message.document.file_id
        media_type = "document"
    else:
        await message.answer("⚠️ Iltimos, video fayl yuboring (yoki /cancel).")
        return

    caption = (message.caption or "").strip()
    await state.update_data(file_id=file_id, caption=caption, media_type=media_type)
    await message.answer("✍️ Ushbu kino uchun kod kiriting (masalan: 105 yoki A12):")
    await state.set_state(AdminStates.waiting_for_movie_code)


@router.message(AdminStates.waiting_for_movie_code, F.text)
async def process_admin_movie_code(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    raw_code = message.text.strip()
    if not MOVIE_CODE_REGEX.fullmatch(raw_code):
        await message.answer(
            "⚠️ Kod faqat harf, raqam, `-` yoki `_` dan iborat bo'lishi kerak (masalan: `105` yoki `A12`). Qayta kiriting:"
        )
        return

    data = await state.get_data()
    saved_code = await db.save_movie(
        movie_code=raw_code,
        file_id=data["file_id"],
        caption=data.get("caption", ""),
        media_type=data.get("media_type", "video"),
    )
    await state.clear()
    await message.answer(
        f"✅ Kino muvaffaqiyatli saqlandi! Kodi: <code>{saved_code}</code>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return

    await callback.message.answer(
        "📢 Barcha foydalanuvchilarga yuboriladigan xabarni (matn, rasm yoki video) jo'nating.\n"
        "Bekor qilish uchun /cancel yuboring."
    )
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast)
async def process_admin_broadcast(message: types.Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.clear()
    user_ids = await db.get_all_user_ids()
    if not user_ids:
        await message.answer("Bazada foydalanuvchilar topilmadi.")
        return

    status_msg = await message.answer(
        f"📤 Xabar {len(user_ids)} ta foydalanuvchiga yuborilmoqda..."
    )

    sent_count = 0
    failed_count = 0
    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            sent_count += 1
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await message.copy_to(chat_id=uid)
                sent_count += 1
            except Exception:
                failed_count += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed_count += 1
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", uid, exc)
            failed_count += 1

    await status_msg.edit_text(
        f"✅ Reklama yuborish yakunlandi!\n"
        f"📨 Yetkazildi: <b>{sent_count}</b> ta\n"
        f"❌ Xatolik / bloklagan: <b>{failed_count}</b> ta",
        parse_mode="HTML",
    )
