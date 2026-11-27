from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_MOVIE_SEARCH = "🎬 Kino kod bo‘yicha izlash"
BTN_MEDIA_DOWNLOAD = "📥 Video / Musiqa yuklash (Link orqali)"

SERIES_EPISODES_PER_PAGE = 16


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MOVIE_SEARCH)],
            [KeyboardButton(text=BTN_MEDIA_DOWNLOAD)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Kino kodi yoki video linkini yuboring...",
    )


def get_download_format_keyboard(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Video (MP4)",
                    callback_data=f"dl:mp4:{req_id}",
                ),
                InlineKeyboardButton(
                    text="🎵 Musiqa / Audio (MP3)",
                    callback_data=f"dl:mp3:{req_id}",
                ),
            ]
        ]
    )


def get_extract_music_keyboard(req_id: str) -> InlineKeyboardMarkup:
    """Button attached directly underneath a downloaded video to extract its audio."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔊 Videodagi musiqani yuklab olish",
                    callback_data=f"dl:mp3:{req_id}",
                )
            ]
        ]
    )


def get_single_movie_keyboard(
    movie_code: str, bot_username: str = ""
) -> InlineKeyboardMarkup:
    from urllib.parse import quote

    share_link = (
        f"https://t.me/{bot_username}?start=movie_{movie_code}"
        if bot_username
        else "https://t.me"
    )
    share_url = (
        f"https://t.me/share/url?url={quote(share_link, safe='')}"
        f"&text={quote(f'🎬 Kino kodi: {movie_code}', safe='')}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📋 Kod: {movie_code}",
                    copy_text=CopyTextButton(text=movie_code),
                ),
                InlineKeyboardButton(text="📤 Ulashish", url=share_url),
            ]
        ]
    )


def get_series_episodes_keyboard(
    movie_code: str,
    total_episodes: int,
    current_ep: int = 1,
    page: int = 0,
    per_page: int = SERIES_EPISODES_PER_PAGE,
    bot_username: str = "",
) -> InlineKeyboardMarkup:
    from urllib.parse import quote

    total_pages = max(1, (total_episodes + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start_ep = page * per_page + 1
    end_ep = min(total_episodes, start_ep + per_page - 1)

    rows = []
    row = []
    for ep_num in range(start_ep, end_ep + 1):
        label = f"▶️ {ep_num}-qism" if ep_num == current_ep else f"{ep_num}-qism"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"ep:{movie_code}:{ep_num}:{page}",
            )
        )
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️ Oldingi",
                    callback_data=f"eppage:{movie_code}:{page - 1}:{current_ep}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"📄 {page + 1}/{total_pages}",
                callback_data="epnoop",
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="Keyingi ▶️",
                    callback_data=f"eppage:{movie_code}:{page + 1}:{current_ep}",
                )
            )
        rows.append(nav)

    batch_label = (
        f"📥 Barcha qismlarni yuborish ({total_episodes} ta)"
        if total_pages == 1
        else f"📥 {start_ep}–{end_ep}-qismlarni ketma-ket yuborish"
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=batch_label,
                callback_data=f"epall:{movie_code}:{page}",
            )
        ]
    )

    share_link = (
        f"https://t.me/{bot_username}?start=movie_{movie_code}"
        if bot_username
        else "https://t.me"
    )
    share_url = (
        f"https://t.me/share/url?url={quote(share_link, safe='')}"
        f"&text={quote(f'📺 Serial kodi: {movie_code} ({total_episodes} ta qism)', safe='')}"
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"📋 Kod: {movie_code}",
                copy_text=CopyTextButton(text=movie_code),
            ),
            InlineKeyboardButton(text="📤 Ulashish", url=share_url),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Kino yuklash", callback_data="admin_upload_movie")],
            [InlineKeyboardButton(text="🎬 Kinolar ro'yxati", callback_data="admin_movies_list")],
            [InlineKeyboardButton(text="🔄 Bazani yangilash (Sync)", callback_data="admin_sync")],
            [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Reklama yuborish", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="⚙️ Bot sozlamalari (API 10.5)", callback_data="admin_bot_settings")],
        ]
    )
