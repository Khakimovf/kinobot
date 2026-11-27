import asyncio
import html
import json
import logging
import shutil
import zlib
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp
from aiogram import Bot, types
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

import config
from keyboards import get_download_format_keyboard

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_WIDGET_RAW_BYTES = 32_768
MAX_WIDGET_ZLIB_BYTES = 4_096


class TelegramRawAPIError(Exception):
    """Raised when a direct Bot API HTTP request returns ok=False."""

    def __init__(self, method: str, error_code: Optional[int], description: str) -> None:
        self.method = method
        self.error_code = error_code
        self.description = description
        super().__init__(f"{method} failed ({error_code}): {description}")


async def call_telegram_api(
    method: str,
    payload: Dict[str, Any],
    token: Optional[str] = None,
    timeout_seconds: int = 15,
) -> Dict[str, Any]:
    """
    Send a direct JSON request to Telegram Bot API via aiohttp.
    Ensures Bot API 10.3 / 10.4 methods work cleanly even if wrapper versions differ.
    """
    bot_token = (token or config.BOT_TOKEN).strip()
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    clean_payload = {k: v for k, v in payload.items() if v is not None}

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=clean_payload) as response:
            try:
                body = await response.json()
            except Exception as exc:
                text = await response.text()
                raise TelegramRawAPIError(method, response.status, text[:200]) from exc

            if not body.get("ok"):
                raise TelegramRawAPIError(
                    method=method,
                    error_code=body.get("error_code", response.status),
                    description=body.get("description", "Unknown Telegram API error"),
                )
            return body["result"]


def verify_widget_html_limits(widget_html: str) -> tuple[int, int]:
    """
    Validate Bot API 10.4 HTML widget size limits:
    - Raw UTF-8 <= 32,768 bytes
    - Zlib max-compression <= 4,096 bytes
    Returns (raw_bytes, compressed_bytes).
    """
    raw = widget_html.encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    if len(raw) > MAX_WIDGET_RAW_BYTES:
        raise ValueError(
            f"Widget HTML too large: {len(raw)} bytes (max {MAX_WIDGET_RAW_BYTES})"
        )
    if len(compressed) > MAX_WIDGET_ZLIB_BYTES:
        raise ValueError(
            f"Compressed widget HTML too large: {len(compressed)} bytes (max {MAX_WIDGET_ZLIB_BYTES})"
        )
    return len(raw), len(compressed)


def build_download_widget_html(platform_name: str, req_id: str) -> str:
    """
    Build a compact (< 4 KB) self-contained Bot API 10.4 HTML/CSS/JS interactive widget.
    Communicates with the bot via window.TelegramWidget.request() and reacts to
    live state updates pushed via editMessageWidgetData (chatDataChanged).
    """
    safe_platform = html.escape(platform_name)
    safe_req_id = html.escape(req_id)

    widget_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:12px;background:var(--tg-theme-secondary-bg-color,#1c242f);color:var(--tg-theme-text-color,#fff)}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.brand{{font-weight:700;font-size:14px}}
.badge{{font-size:11px;padding:3px 8px;border-radius:99px;background:rgba(36,129,204,.22);color:var(--tg-theme-link-color,#6ab3f3)}}
.status{{font-size:12px;color:var(--tg-theme-hint-color,#9aa7b8);margin-bottom:10px;min-height:16px}}
.bar{{height:4px;border-radius:4px;background:rgba(255,255,255,.12);overflow:hidden;margin-bottom:10px;display:none}}
.bar.on{{display:block}}
.fill{{height:100%;width:35%;background:var(--tg-theme-button-color,#2481cc);border-radius:4px;transition:width .3s ease}}
.row{{display:flex;gap:8px}}
button{{flex:1;border:0;border-radius:10px;padding:10px 8px;font-size:13px;font-weight:600;cursor:pointer;background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff);transition:opacity .15s}}
button.alt{{background:rgba(128,128,128,.22);color:var(--tg-theme-text-color,#fff)}}
button:disabled{{opacity:.5;cursor:default}}
</style>
</head>
<body>
<div class="top"><span class="brand">📥 {safe_platform}</span><span class="badge" id="bdg">Tayyor</span></div>
<div class="status" id="st">Yuklash formatini tanlang:</div>
<div class="bar" id="bar"><div class="fill" id="fill"></div></div>
<div class="row">
<button id="b4">🎬 Video MP4</button>
<button id="b3" class="alt">🎵 Audio MP3</button>
</div>
<script>
const W=window.TelegramWidget;
const REQ="{safe_req_id}";
const st=document.getElementById("st"),bdg=document.getElementById("bdg"),bar=document.getElementById("bar"),fill=document.getElementById("fill"),b4=document.getElementById("b4"),b3=document.getElementById("b3");
const MAP={{idle:["Tayyor",0,false],queue:["Navbatda",25,true],loading:["Yuklanmoqda",55,true],uploading:["Yuborilmoqda",85,true],cached:["Kesh ⚡",100,false],done:["Tayyor ✅",100,false],cooldown:["Kuting ⏳",0,false],error:["Xatolik ❌",0,false]}};
function render(raw){{
  let s={{st:"idle",msg:"Yuklash formatini tanlang:"}};
  try{{if(raw)s=Object.assign(s,JSON.parse(raw));}}catch(e){{}}
  const m=MAP[s.st]||MAP.idle;
  bdg.textContent=m[0];
  st.textContent=s.msg||"";
  bar.className=m[2]?"bar on":"bar";
  fill.style.width=m[1]+"%";
  const busy=s.st==="queue"||s.st==="loading"||s.st==="uploading";
  b4.disabled=busy;b3.disabled=busy;
}}
if(W&&W.onEvent){{W.onEvent("chatDataChanged",()=>render(W.chatData));}}
render(W&&W.chatData);
async function pick(fmt){{
  if(!W||!W.request)return;
  render(JSON.stringify({{st:"loading",msg:"So'rov yuborilmoqda..."}}));
  try{{
    const res=await W.request("dl:"+fmt+":"+REQ);
    if(res)render(typeof res==="string"?res:JSON.stringify(res));
  }}catch(err){{
    render(JSON.stringify({{st:"error",msg:"Ulanish xatosi. Pastdagi tugmalardan foydalaning."}}));
  }}
}}
b4.onclick=()=>pick("mp4");
b3.onclick=()=>pick("mp3");
</script>
</body>
</html>"""

    verify_widget_html_limits(widget_html)
    return widget_html


def build_download_rich_payload(platform_name: str, req_id: str) -> Dict[str, Any]:
    """
    Build a Bot API 10.3 InputRichMessage payload with inline text-embedded buttons
    (RichTextButton) and a button row (InputRichBlockButtons) as Tier-2 fallback.
    """
    return {
        "blocks": [
            {
                "type": "paragraph",
                "text": [
                    "🔗 ",
                    {"type": "bold", "text": platform_name},
                    " havolasi qabul qilindi.\nYuklash formatini tanlang: ",
                    {
                        "type": "button",
                        "button": {
                            "text": "🎬 Video MP4",
                            "style": "primary",
                            "callback_data": f"dl:mp4:{req_id}",
                        },
                    },
                    "  ",
                    {
                        "type": "button",
                        "button": {
                            "text": "🎵 Audio MP3",
                            "style": "success",
                            "callback_data": f"dl:mp3:{req_id}",
                        },
                    },
                ],
            }
        ]
    }


async def send_download_widget(
    bot: Bot,
    chat_id: int,
    platform_name: str,
    req_id: str,
    is_private_chat: bool = True,
) -> Dict[str, Any]:
    """
    Send the interactive download selector with automatic 3-tier fallback:
    1) Bot API 10.4 `sendWidget` (HTML/JS widget < 4 KB + inline keyboard fallback for older clients)
    2) Bot API 10.3 `sendRichMessage` (inline text buttons)
    3) Classic `sendMessage` with InlineKeyboardMarkup
    Never raises on unsupported clients or Bot API restrictions.
    """
    fallback_kb = get_download_format_keyboard(req_id)

    # Tier 1: Bot API 10.4 Interactive HTML Widget (supported in private chats)
    if is_private_chat:
        try:
            widget_html = build_download_widget_html(platform_name, req_id)
            initial_state = json.dumps(
                {
                    "st": "idle",
                    "msg": f"{platform_name} — formatni tanlang:",
                },
                ensure_ascii=False,
            )
            result = await call_telegram_api(
                "sendWidget",
                {
                    "chat_id": chat_id,
                    "html": widget_html,
                    "height": 135,
                    "chat_data": initial_state,
                    "caption": f"🔗 <b>{html.escape(platform_name)}</b> — formatni tanlang:",
                    "parse_mode": "HTML",
                    "allow_queries": True,
                    # Also attaches classic inline buttons so older Telegram clients remain usable
                    "reply_markup": fallback_kb.model_dump(exclude_none=True),
                },
                token=bot.token,
            )
            logger.info("Sent Bot API 10.4 download widget (msg_id=%s)", result.get("message_id"))
            return {"mode": "widget", "message_id": result.get("message_id")}
        except Exception as exc:
            logger.warning(
                "Bot API 10.4 sendWidget unavailable (%s); falling back to Bot API 10.3 RichMessage.",
                exc,
            )

    # Tier 2: Bot API 10.3 Rich Message with inline text-embedded buttons
    try:
        result = await call_telegram_api(
            "sendRichMessage",
            {
                "chat_id": chat_id,
                "rich_message": build_download_rich_payload(platform_name, req_id),
            },
            token=bot.token,
        )
        logger.info("Sent Bot API 10.3 download rich message (msg_id=%s)", result.get("message_id"))
        return {"mode": "rich", "message_id": result.get("message_id")}
    except Exception as exc:
        logger.warning(
            "Bot API 10.3 sendRichMessage unavailable (%s); falling back to classic InlineKeyboard.",
            exc,
        )

    # Tier 3: Classic InlineKeyboard fallback (guaranteed compatibility)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔗 <b>{html.escape(platform_name)}</b> havolasi qabul qilindi.\n"
            f"Yuklash formatini tanlang:"
        ),
        parse_mode="HTML",
        reply_markup=fallback_kb,
    )
    return {"mode": "inline", "message_id": sent.message_id}


async def answer_widget_query(
    bot: Bot,
    widget_query_id: str,
    state: Dict[str, Any],
) -> bool:
    """Answer a Bot API 10.4 WidgetQuery within Telegram's 15-second window."""
    data_str = json.dumps(state, ensure_ascii=False)[:1024]
    try:
        await call_telegram_api(
            "answerWidgetQuery",
            {
                "widget_query_id": widget_query_id,
                "data": data_str,
            },
            token=bot.token,
        )
        return True
    except Exception as exc:
        logger.warning("answerWidgetQuery failed: %s", exc)
        return False


async def update_widget_state(
    bot: Bot,
    chat_id: int,
    message_id: int,
    state: Dict[str, Any],
) -> bool:
    """Push live state changes into a Bot API 10.4 widget via editMessageWidgetData."""
    data_str = json.dumps(state, ensure_ascii=False)[:1024]
    try:
        await call_telegram_api(
            "editMessageWidgetData",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "chat_data": data_str,
            },
            token=bot.token,
        )
        return True
    except Exception as exc:
        logger.debug("editMessageWidgetData skipped/failed: %s", exc)
        return False


def build_movie_rich_payload(
    movie_code: str,
    caption: str,
    bot_username: Optional[str] = None,
    total_episodes: int = 1,
    current_episode: int = 1,
) -> Dict[str, Any]:
    """
    Build a Bot API 10.3 InputRichMessage with inline text-embedded interactive buttons
    (RichTextButton) directly attached to keywords:
    - Movie code -> one-tap clipboard copy (`copy_text`)
    - Share -> Telegram share sheet with deep-link
    - Search another movie / Random movie -> inline callback links (`style: 'link'`)
    """
    share_link = (
        f"https://t.me/{bot_username}?start=movie_{movie_code}"
        if bot_username
        else "https://t.me"
    )
    share_url = (
        f"https://t.me/share/url?url={quote(share_link, safe='')}"
        f"&text={quote(f'🎬 Kino kodi: {movie_code}', safe='')}"
    )

    blocks = []

    if total_episodes > 1:
        header_parts: list[Any] = [
            "📺 ",
            {"type": "bold", "text": f"Serial — {total_episodes} ta qism"},
            f" (hozir: {current_episode}-qism)",
        ]
    else:
        header_parts = [
            "🎬 ",
            {"type": "bold", "text": "Kino ma'lumotlari"},
        ]

    cleaned_caption = (caption or "").strip()
    if cleaned_caption and cleaned_caption.upper() != movie_code.upper():
        short_desc = cleaned_caption[:160] + ("..." if len(cleaned_caption) > 160 else "")
        header_parts.extend(["\n📝 ", short_desc])

    blocks.append({"type": "paragraph", "text": header_parts})
    blocks.append({"type": "divider"})

    # Bot API 10.3: RichTextButton embedded directly inline inside message text!
    blocks.append(
        {
            "type": "paragraph",
            "text": [
                "📌 Kino kodi: ",
                {
                    "type": "button",
                    "button": {
                        "text": f"📋 {movie_code}",
                        "style": "primary",
                        "copy_text": {"text": movie_code},
                    },
                },
                "  |  Do'stlarga: ",
                {
                    "type": "button",
                    "button": {
                        "text": "📤 Ulashish",
                        "style": "success",
                        "url": share_url,
                    },
                },
                "\n🔎 Davom etish: ",
                {
                    "type": "button",
                    "button": {
                        "text": "Boshqa kino izlash",
                        "style": "link",
                        "callback_data": "movie:search_more",
                    },
                },
                " • ",
                {
                    "type": "button",
                    "button": {
                        "text": "Tasodifiy kino 🎲",
                        "style": "link",
                        "callback_data": "movie:random",
                    },
                },
            ],
        }
    )

    return {"blocks": blocks}


def _build_movie_fallback_keyboard(
    movie_code: str, bot_username: Optional[str] = None
) -> InlineKeyboardMarkup:
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
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Boshqa kino izlash",
                    callback_data="movie:search_more",
                ),
                InlineKeyboardButton(
                    text="🎲 Tasodifiy kino",
                    callback_data="movie:random",
                ),
            ],
        ]
    )


async def send_movie_rich_card(
    bot: Bot,
    chat_id: int,
    movie_code: str,
    caption: str = "",
    total_episodes: int = 1,
    current_episode: int = 1,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Dict[str, Any]:
    """
    Send movie/series info card with Bot API 10.3 text-embedded interactive buttons.
    For multi-episode series, also attaches the interactive episode selector keyboard.
    Automatically falls back to a standard HTML message + InlineKeyboard if unavailable.
    """
    bot_username: Optional[str] = None
    try:
        me = await bot.me()
        bot_username = me.username
    except Exception:
        pass

    try:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": build_movie_rich_payload(
                movie_code=movie_code,
                caption=caption,
                bot_username=bot_username,
                total_episodes=total_episodes,
                current_episode=current_episode,
            ),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup.model_dump(exclude_none=True)

        result = await call_telegram_api(
            "sendRichMessage",
            payload,
            token=bot.token,
        )
        logger.info("Sent Bot API 10.3 movie rich card (code=%s, eps=%d)", movie_code, total_episodes)
        return {"mode": "rich", "message_id": result.get("message_id")}
    except Exception as exc:
        logger.warning(
            "Bot API 10.3 sendRichMessage failed for movie '%s' (%s); using InlineKeyboard fallback.",
            movie_code,
            exc,
        )

    if total_episodes > 1:
        fallback_text = (
            f"📺 <b>Serial ({total_episodes} ta qism)</b> — Kod: <code>{html.escape(movie_code)}</code>\n"
            f"Quyidan kerakli qismni tanlang:"
        )
    else:
        fallback_text = f"🎬 <b>Kino kodi:</b> <code>{html.escape(movie_code)}</code>"

    cleaned = (caption or "").strip()
    if cleaned and cleaned.upper() != movie_code.upper():
        fallback_text += f"\n📝 {html.escape(cleaned[:200])}"

    sent = await bot.send_message(
        chat_id=chat_id,
        text=fallback_text,
        parse_mode="HTML",
        reply_markup=reply_markup or _build_movie_fallback_keyboard(movie_code, bot_username),
    )
    return {"mode": "inline", "message_id": sent.message_id}


async def apply_bot_settings(
    bot: Bot,
    overrides: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Configure bot settings programmatically via Bot API 10.5 `setMySettings`.
    Uses aiogram's native method when available and falls back to raw aiohttp API call.
    """
    payload: Dict[str, Any] = (
        dict(overrides)
        if overrides is not None
        else {
            "supports_inline_queries": config.BOT_ENABLE_INLINE,
            "inline_query_placeholder": config.BOT_INLINE_PLACEHOLDER,
            "can_join_groups": config.BOT_CAN_JOIN_GROUPS,
            "has_topics_enabled": config.BOT_TOPICS_ENABLED,
            "has_topic_sidebar": config.BOT_TOPIC_SIDEBAR,
            "callback_query_mode": "default",
        }
    )

    try:
        if hasattr(bot, "set_my_settings"):
            await bot.set_my_settings(**payload)
        else:
            await call_telegram_api("setMySettings", payload, token=bot.token)
        logger.info("Bot API 10.5 setMySettings qo'llandi: %s", payload)
        return True
    except Exception as exc:
        logger.warning("Bot API 10.5 setMySettings xatosi (%s)", exc)
        return False


async def format_bot_settings_panel(bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    """
    Fetch live Bot API 10.5 `BotFullInfo` via getMe() and build an admin control
    panel with one-tap `setMySettings` toggle buttons and the `botinvite` link.
    """
    info = await bot.get_me()

    def _flag(val: bool) -> str:
        return "✅ Yoqilgan" if val else "❌ O'chirilgan"

    username = info.username or ""
    invite_line = (
        f"🔗 <b>Tezkor taklif (10 tagacha):</b> <code>https://t.me/botinvite/{username}</code>\n"
        if username
        else ""
    )
    placeholder = html.escape(info.inline_query_placeholder or "(standart)")

    text = (
        "⚙️ <b>Bot sozlamalari (Bot API 10.5)</b>\n\n"
        f"🤖 Bot: <b>{html.escape(info.first_name)}</b> (@{html.escape(username)})\n"
        f"🔍 Inline rejim: {_flag(info.supports_inline_queries)}\n"
        f"💬 Inline placeholder: <code>{placeholder}</code>\n"
        f"👥 Guruhlarga qo'shish: {_flag(info.can_join_groups)}\n"
        f"🗂 Shaxsiy chat topiclari: {_flag(info.has_topics_enabled)}\n"
        f"📑 Topic sidebar: {_flag(info.has_topic_sidebar)}\n"
        f"{invite_line}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "🔍 Inline: O'chirish"
                        if info.supports_inline_queries
                        else "🔍 Inline: Yoqish"
                    ),
                    callback_data=f"botset:inline:{0 if info.supports_inline_queries else 1}",
                ),
                InlineKeyboardButton(
                    text=(
                        "👥 Guruhlar: O'chirish"
                        if info.can_join_groups
                        else "👥 Guruhlar: Yoqish"
                    ),
                    callback_data=f"botset:groups:{0 if info.can_join_groups else 1}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "🗂 Topiclar: O'chirish"
                        if info.has_topics_enabled
                        else "🗂 Topiclar: Yoqish"
                    ),
                    callback_data=f"botset:topics:{0 if info.has_topics_enabled else 1}",
                ),
                InlineKeyboardButton(
                    text=(
                        "📑 Sidebar: O'chirish"
                        if info.has_topic_sidebar
                        else "📑 Sidebar: Yoqish"
                    ),
                    callback_data=f"botset:sidebar:{0 if info.has_topic_sidebar else 1}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 .env standartlarini qo'llash",
                    callback_data="botset:apply_env:1",
                )
            ],
        ]
    )
    return text, kb


async def upload_media_fast(
    bot: Bot,
    chat_id: int,
    file_path: Path,
    media_format: str,
    title: str = "",
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    duration: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    timeout_seconds: int = 300,
) -> Optional[str]:
    """
    Upload a local media file to Telegram using native `curl` (exact Content-Length,
    HTTP/2, native OS TLS — avoids Windows aiohttp ProactorEventLoop chunked upload hangs).
    Supports HTML caption and InlineKeyboardMarkup in the SAME video bubble.
    """
    curl_exe = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_exe:
        from aiogram.types import FSInputFile

        input_file = FSInputFile(file_path)
        if media_format == "mp3":
            sent = await bot.send_audio(
                chat_id=chat_id,
                audio=input_file,
                caption=caption,
                parse_mode=parse_mode if caption else None,
                title=title or None,
                duration=duration,
                reply_markup=reply_markup,
                request_timeout=timeout_seconds,
            )
            return sent.audio.file_id if sent.audio else (sent.document.file_id if sent.document else None)
        sent = await bot.send_video(
            chat_id=chat_id,
            video=input_file,
            caption=caption,
            parse_mode=parse_mode if caption else None,
            duration=duration,
            width=width,
            height=height,
            supports_streaming=True,
            reply_markup=reply_markup,
            request_timeout=timeout_seconds,
        )
        return sent.video.file_id if sent.video else (sent.document.file_id if sent.document else None)

    method = "sendAudio" if media_format == "mp3" else "sendVideo"
    field_name = "audio" if media_format == "mp3" else "video"
    url = f"{TELEGRAM_API_BASE}/bot{bot.token}/{method}"

    cmd = [
        curl_exe,
        "-sS",
        "--connect-timeout",
        "20",
        "--max-time",
        str(timeout_seconds),
        "-F",
        f"chat_id={chat_id}",
    ]
    if caption:
        cmd.extend(["-F", f"caption={caption}"])
        if parse_mode:
            cmd.extend(["-F", f"parse_mode={parse_mode}"])
    if duration is not None:
        cmd.extend(["-F", f"duration={duration}"])
    if reply_markup is not None:
        markup_json = json.dumps(
            reply_markup.model_dump(exclude_none=True), ensure_ascii=False
        )
        cmd.extend(["-F", f"reply_markup={markup_json}"])
    if media_format == "mp3":
        if title:
            cmd.extend(["-F", f"title={title}"])
    else:
        cmd.extend(["-F", "supports_streaming=true"])
        if width is not None:
            cmd.extend(["-F", f"width={width}"])
        if height is not None:
            cmd.extend(["-F", f"height={height}"])

    cmd.extend(["-F", f"{field_name}=@{file_path}", url])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").replace(bot.token, "***").strip()

    if proc.returncode != 0:
        raise TelegramRawAPIError(
            method=method,
            error_code=proc.returncode,
            description=stderr_text or f"curl exited with code {proc.returncode}",
        )

    try:
        body = json.loads(stdout_text)
    except Exception as exc:
        raise TelegramRawAPIError(method, None, f"Invalid JSON response: {stdout_text[:160]}") from exc

    if not body.get("ok"):
        raise TelegramRawAPIError(
            method=method,
            error_code=body.get("error_code"),
            description=body.get("description", "Upload failed"),
        )

    result = body.get("result") or {}
    if media_format == "mp3":
        media_obj = result.get("audio") or result.get("document") or {}
    else:
        media_obj = result.get("video") or result.get("document") or {}
    return media_obj.get("file_id")
