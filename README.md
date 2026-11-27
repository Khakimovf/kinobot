# Universal Telegram Kinobot & Media Downloader

`aiogram 3.x`, `aiosqlite` va `yt-dlp` asosida qurilgan universal Telegram bot:
1. **Kino kod bo‘yicha izlash** (`105`, `A12` kabi raqamli va harf-raqamli kodlar).
2. **Kanaldan 2 bosqichli jim avtomatik saqlash**: kanalga avval **Video**, ketidan alohida xabar bilan **Kino kodi** yuboriladi — bot kanalni spamlamasdan bazaga saqlaydi va xohishga ko‘ra adminga shaxsiy xabar yuboradi.
3. **YouTube, Instagram, TikTok yuklovchi** (`🎬 Video (MP4)` / `🎵 Musiqa / Audio (MP3)`) + **Anti-Ban himoyasi**:
   - SQLite `media_cache` (`url + format` bo‘yicha Telegram `file_id` keshlash)
   - `cookies.txt` ni avtomatik ulash + real brauzer `User-Agent`
   - Foydalanuvchi bo‘yicha cooldown (20 soniya) va global `asyncio.Semaphore(2)` navbat cheklovi
   - 50 MB Telegram limiti nazorati, `asyncio.to_thread` (non-blocking), `downloads/` vaqtinchalik fayllarini darhol tozalash

## Arxitektura

```text
kinobot/
├── .env / .env.example     # BOT_TOKEN, CHANNEL_ID, ADMIN_ID va anti-ban sozlamalari
├── api_widgets.py          # Bot API 10.3 (RichMessage text-buttons) & 10.4 (HTML/JS Widget) + fallback
├── config.py               # .env yuklash va konfiguratsiya
├── database.py             # SQLite (kinobot.db bilan to'liq mos + avtomatik migratsiya)
├── downloader.py           # yt-dlp servisi (kesh key, cookies, UA, cooldown, semaphore, cleanup)
├── keyboards.py            # ReplyKeyboard va InlineKeyboard tugmalari
├── states.py               # FSM holatlari (UserModes, AdminStates)
├── handlers/
│   ├── __init__.py         # Routerlarni ustuvorlik tartibida ulash
│   ├── channel.py          # Kanalga tashlangan Video + Kod ni avtomatik saqlash
│   ├── admin.py            # Admin panel (/admin, /sync, statistika, reklama, kino yuklash)
│   ├── downloader.py       # Link aniqlash, MP4/MP3 tanlash, kesh va yuklash jarayoni
│   ├── user.py             # /start, menyu rejimlari va kino qidirish
│   └── common.py           # Umumiy yordamchi funksiyalar
├── main.py                 # Botni ishga tushirish nuqtasi
└── requirements.txt
```

## O‘rnatish va ishga tushirish

1. Kutubxonalarni o‘rnating:
   ```bash
   pip install -r requirements.txt
   ```

2. `.env` faylini ochib quyidagi asosiy qiymatlarni to‘ldiring:
   - `BOT_TOKEN` — `@BotFather` dan olingan yangi token
   - `CHANNEL_ID` — kinolar tashlanadigan kanal ID (masalan, `-1001234567890`)
   - `ADMIN_ID` — adminning Telegram ID raqami

3. Botni `CHANNEL_ID` kanaliga **Admin** sifatida qo‘shing.

4. **(Tavsiya etiladi) `ffmpeg` va `cookies.txt`:**
   - **MP3 konvertatsiya** va yuqori sifatli MP4 video+audio birlashtirish uchun `ffmpeg` o‘rnating (Windows: `winget install Gyan.FFmpeg`) yoki `.env` da `FFMPEG_LOCATION` ni ko‘rsating. `ffmpeg` bo‘lmasa ham bot progressive MP4 va native audio rejimida xatosiz ishlaydi.
   - YouTube/Instagram bloklaridan saqlanish uchun brauzerdan eksport qilingan Netscape formatidagi `cookies.txt` faylini loyiha papkasiga qo‘ysangiz, bot uni avtomatik ishlatadi.

5. Botni ishga tushiring:
   ```bash
   python main.py
   ```

## VPS Serverga joylash (24/7)

### 1-usul: Docker (eng oson va tavsiya etiladi)
Docker ichida `ffmpeg`, `curl` va `nodejs` avtomatik o‘rnatiladi, baza va `cookies.txt` esa `./data/` papkasida saqlanadi:
```bash
mkdir -p data
# Mavjud bazangiz va cookies.txt bo'lsa ./data/ ichiga ko'chiring:
# cp kinobot.db data/ && cp cookies.txt data/
docker compose up -d --build
docker compose logs -f
```

### 2-usul: Ubuntu/Debian `systemd` servisi (Docker siz)
```bash
sudo apt update && sudo apt install -y python3-venv ffmpeg curl nodejs
cd /opt/kinobot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo cp kinobot.service /etc/systemd/system/kinobot.service
sudo systemctl daemon-reload
sudo systemctl enable --now kinobot
sudo journalctl -u kinobot -f
```
