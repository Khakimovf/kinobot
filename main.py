import asyncio
import logging
import socket

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

import config
import database as db
from api_widgets import apply_bot_settings
from downloader import media_downloader
from handlers import get_root_router


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("kinobot")

    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_bot_token_here":
        raise RuntimeError(
            "BOT_TOKEN sozlanmagan! Iltimos, .env fayliga haqiqiy BOT_TOKEN kiriting."
        )

    if config.get_ffmpeg_location():
        logger.info("ffmpeg aniqlandi: %s", config.get_ffmpeg_location())
    else:
        logger.warning(
            "ffmpeg topilmadi! MP4 progressive va native audio rejimida ishlaydi. "
            "To'liq MP3 konvertatsiya va HD MP4 merge uchun ffmpeg o'rnating."
        )

    if config.COOKIES_FILE.is_file() and config.COOKIES_FILE.stat().st_size > 0:
        logger.info("cookies.txt topildi va yt-dlp ga ulandi: %s", config.COOKIES_FILE)
    else:
        logger.info(
            "cookies.txt topilmadi (ixtiyoriy). Agar YouTube/Instagram cheklov qo'ysa, "
            "papkaga cookies.txt qo'ying."
        )

    # Prepare temp downloads folder and initialize / migrate SQLite DB
    media_downloader.prepare_downloads_dir()
    await db.init_db()

    session = AiohttpSession(timeout=300)
    session._connector_init["family"] = socket.AF_INET
    session._connector_init["enable_cleanup_closed"] = True
    bot = Bot(token=config.BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(get_root_router())

    if config.AUTO_CONFIGURE_BOT_SETTINGS:
        await apply_bot_settings(bot)

    await bot.delete_webhook(drop_pending_updates=True)
    allowed_updates = dp.resolve_used_update_types()
    logger.info("Kinobot ishga tushdi! Allowed updates: %s", allowed_updates)
    try:
        await dp.start_polling(bot, allowed_updates=allowed_updates)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
