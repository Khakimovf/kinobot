import asyncio

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state

import database as db
from handlers.common import (
    MOVIE_CODE_REGEX,
    build_movie_caption,
    get_bot_username,
    is_admin,
    send_movie_to_user,
)
from keyboards import (
    BTN_MEDIA_DOWNLOAD,
    BTN_MOVIE_SEARCH,
    SERIES_EPISODES_PER_PAGE,
    get_admin_keyboard,
    get_main_menu_keyboard,
    get_series_episodes_keyboard,
    get_single_movie_keyboard,
)
from states import UserModes

menu_router = Router(name="user_menu")
fallback_router = Router(name="user_fallback")


async def _deliver_movie_by_code(
    bot: Bot, message: types.Message, movie_code: str
) -> bool:
    """
    Deliver a movie or series 1st episode in ONE single clean message bubble:
    video + movie code + branded bot info caption + attached buttons (zero extra clutter messages).
    """
    episodes = await db.get_movie_episodes(movie_code)
    if not episodes:
        return False

    first_ep = episodes[0]
    total = len(episodes)
    bot_username = await get_bot_username(bot)

    caption = build_movie_caption(
        movie_code=first_ep["movie_code"],
        raw_caption=first_ep.get("caption", ""),
        bot_username=bot_username,
        total_episodes=total,
        episode_number=1,
    )

    reply_markup = (
        get_series_episodes_keyboard(
            movie_code=first_ep["movie_code"],
            total_episodes=total,
            current_ep=1,
            page=0,
            bot_username=bot_username,
        )
        if total > 1
        else get_single_movie_keyboard(first_ep["movie_code"], bot_username)
    )

    sent = await send_movie_to_user(
        bot,
        message,
        first_ep,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    if not sent:
        await message.answer(
            "⚠️ Videoni yuborishda xatolik yuz berdi. U kanaldan o'chirilgan yoki cheklangan bo'lishi mumkin."
        )
    return True


@menu_router.message(CommandStart())
async def cmd_start(
    message: types.Message,
    state: FSMContext,
    bot: Bot,
    command: CommandObject,
) -> None:
    user_id = message.from_user.id
    await db.add_user(user_id)
    await state.clear()

    # Deep-link support for shared movies: https://t.me/<bot>?start=movie_199
    start_arg = (command.args or "").strip()
    if start_arg.lower().startswith("movie_"):
        shared_code = start_arg[6:].strip()
        if shared_code and MOVIE_CODE_REGEX.fullmatch(shared_code):
            episodes = await db.get_movie_episodes(shared_code)
            if episodes:
                await _deliver_movie_by_code(bot, message, shared_code)
                return

    await message.answer(
        "👋 <b>Xush kelibsiz!</b>\n\n"
        "Hech qanday majburiy obunalarsiz universal kino va video yuklovchi bot!\n\n"
        "• 🎬 <b>Kino kodini</b> yuboring (masalan: <code>199</code> yoki <code>A12</code>)\n"
        "• 📥 <b>YouTube / Instagram / TikTok</b> havolasini yuboring.",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )

    if is_admin(user_id):
        await message.answer(
            "🛠 Admin boshqaruv paneli (/admin):",
            reply_markup=get_admin_keyboard(),
        )


@menu_router.message(F.text == BTN_MOVIE_SEARCH)
async def mode_movie_search(message: types.Message, state: FSMContext) -> None:
    await db.add_user(message.from_user.id)
    await state.set_state(UserModes.movie_search)
    await message.answer(
        "🎬 <b>Kino kod bo‘yicha izlash rejimi yoqildi.</b>\n\n"
        "Menga kino yoki serial kodini yuboring (masalan: <code>199</code> yoki <code>A12</code>), "
        "men esa uni barcha qismlari bilan darhol topib beraman.",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )


@menu_router.message(F.text == BTN_MEDIA_DOWNLOAD)
async def mode_media_download(message: types.Message, state: FSMContext) -> None:
    await db.add_user(message.from_user.id)
    await state.set_state(UserModes.media_download)
    await message.answer(
        "📥 <b>Video / Musiqa yuklash rejimi yoqildi.</b>\n\n"
        "Menga <b>YouTube</b>, <b>Instagram</b> yoki <b>TikTok</b> havolasini (<code>https://...</code>) yuboring.\n"
        "Video bilan birga 🔊 <b>Videodagi musiqani yuklab olish</b> tugmasi ham chiqadi.",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )


@menu_router.callback_query(F.data == "epnoop")
async def cb_ep_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


@menu_router.callback_query(F.data.startswith("ep:"))
async def cb_select_episode(callback: types.CallbackQuery, bot: Bot) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    _, movie_code, raw_ep, raw_page = parts
    try:
        ep_num = int(raw_ep)
        page = int(raw_page)
    except ValueError:
        await callback.answer("Noto'g'ri qism raqami.", show_alert=True)
        return

    episodes = await db.get_movie_episodes(movie_code)
    target = next((e for e in episodes if e["episode_number"] == ep_num), None)
    if not target:
        await callback.answer("Ushbu qism topilmadi.", show_alert=True)
        return

    await callback.answer(f"🎬 {ep_num}-qism yuborilmoqda...")
    if callback.message:
        bot_username = await get_bot_username(bot)
        ep_caption = build_movie_caption(
            movie_code=target["movie_code"],
            raw_caption=target.get("caption", ""),
            bot_username=bot_username,
            total_episodes=len(episodes),
            episode_number=ep_num,
        )
        new_kb = get_series_episodes_keyboard(
            movie_code=target["movie_code"],
            total_episodes=len(episodes),
            current_ep=ep_num,
            page=page,
            bot_username=bot_username,
        )
        sent = await send_movie_to_user(
            bot,
            callback.message,
            target,
            caption=ep_caption,
            parse_mode="HTML",
            reply_markup=new_kb,
        )
        if not sent:
            await callback.message.answer(
                f"⚠️ {ep_num}-qismni yuborishda xatolik yuz berdi."
            )


@menu_router.callback_query(F.data.startswith("eppage:"))
async def cb_episode_page(callback: types.CallbackQuery, bot: Bot) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return

    _, movie_code, raw_page, raw_curr = parts
    try:
        page = int(raw_page)
        curr = int(raw_curr)
    except ValueError:
        await callback.answer()
        return

    episodes = await db.get_movie_episodes(movie_code)
    if not episodes:
        await callback.answer("Serial topilmadi.", show_alert=True)
        return

    bot_username = await get_bot_username(bot)
    new_kb = get_series_episodes_keyboard(
        movie_code=episodes[0]["movie_code"],
        total_episodes=len(episodes),
        current_ep=curr,
        page=page,
        bot_username=bot_username,
    )
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=new_kb)
        except TelegramBadRequest:
            pass
    await callback.answer()


@menu_router.callback_query(F.data.startswith("epall:"))
async def cb_send_all_episodes(callback: types.CallbackQuery, bot: Bot) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    _, movie_code, raw_page = parts
    try:
        page = int(raw_page)
    except ValueError:
        page = 0

    episodes = await db.get_movie_episodes(movie_code)
    if not episodes:
        await callback.answer("Serial topilmadi.", show_alert=True)
        return

    start_idx = page * SERIES_EPISODES_PER_PAGE
    batch = episodes[start_idx : start_idx + SERIES_EPISODES_PER_PAGE]
    await callback.answer(f"📥 {len(batch)} ta qism ketma-ket yuborilmoqda...")

    if callback.message:
        bot_username = await get_bot_username(bot)
        total = len(episodes)
        for ep in batch:
            ep_caption = build_movie_caption(
                movie_code=ep["movie_code"],
                raw_caption=ep.get("caption", ""),
                bot_username=bot_username,
                total_episodes=total,
                episode_number=ep["episode_number"],
            )
            await send_movie_to_user(
                bot,
                callback.message,
                ep,
                caption=ep_caption,
                parse_mode="HTML",
            )
            await asyncio.sleep(0.35)


@menu_router.callback_query(F.data == "movie:search_more")
async def cb_movie_search_more(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(UserModes.movie_search)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🔎 Boshqa kino kodini yuboring (masalan: <code>199</code> yoki <code>A12</code>):",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )


@menu_router.callback_query(F.data == "movie:random")
async def cb_movie_random(callback: types.CallbackQuery, bot: Bot) -> None:
    movie = await db.get_random_movie()
    if not movie:
        await callback.answer("Bazada hozircha kinolar yo'q.", show_alert=True)
        return
    await callback.answer(f"🎲 Tasodifiy kino: {movie['movie_code']}")
    if callback.message:
        await _deliver_movie_by_code(bot, callback.message, movie["movie_code"])


@fallback_router.message(
    StateFilter(default_state, UserModes.movie_search),
    F.text,
)
async def process_movie_code(message: types.Message, bot: Bot) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    user_id = message.from_user.id
    await db.add_user(user_id)

    if not MOVIE_CODE_REGEX.fullmatch(text):
        await message.answer(
            "🔎 Kino topish uchun faqat kino kodini yuboring (masalan: <code>199</code> yoki <code>A12</code>),\n"
            "yoki video/musiqa yuklash uchun <code>https://...</code> havola yuboring.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    found = await _deliver_movie_by_code(bot, message, text)
    if not found:
        await message.answer(
            f"❌ <code>{text.upper()}</code> kodi bilan kino topilmadi.",
            parse_mode="HTML",
        )


@fallback_router.message(UserModes.media_download, F.text)
async def handle_non_link_in_download_mode(message: types.Message) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    await message.answer(
        "📥 Iltimos, <b>YouTube</b>, <b>Instagram</b> yoki <b>TikTok</b> havolasini (<code>https://...</code>) yuboring.\n"
        f"Kino izlash uchun pastdagi <b>{BTN_MOVIE_SEARCH}</b> tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
