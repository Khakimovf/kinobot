from aiogram import Router

from handlers import admin, channel, downloader, user


def get_root_router() -> Router:
    root = Router(name="root")
    # Strict registration order:
    # 1. Channel posts (silent auto-intake)
    # 2. /start and main ReplyKeyboard mode switchers (always responsive)
    # 3. Admin commands, callbacks, and AdminStates FSM (so broadcasts with links aren't hijacked)
    # 4. Social media URL detector & inline format callbacks (works from any user mode)
    # 5. Movie code lookup & helpful fallback prompts
    root.include_router(channel.router)
    root.include_router(user.menu_router)
    root.include_router(admin.router)
    root.include_router(downloader.router)
    root.include_router(user.fallback_router)
    return root
