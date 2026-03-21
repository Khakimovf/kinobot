# Telegram Kinobot

A full-featured movie-sharing bot built with aiogram 3.x and aiosqlite.

## Setup Instructions

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Edit `main.py` and update the following configuration variables:
   - `API_TOKEN`: Your bot token from BotFather.
   - `ADMIN_ID`: Keep it as `670167033`, or change if needed.
   - `REQUIRED_CHANNELS`: List of channels users must subscribe to (e.g., `['@my_channel']`).
   - `SYNC_CHANNEL_ID`: The internal Channel ID where files are uploaded/synced from.

3. Make sure the bot is added as an **administrator** in both the required channels and the syncing channel.

4. Run the bot:
   ```bash
   python main.py
   ```

## Features

- **Mandatory Subscription:** Enforces users to subscribe to channels before fetching movies.
- **Auto Channel Scanner:** The `/sync` command scans older messages (with a backward ID scan workaround).
- **Auto Intake:** When a video is posted with a numeric caption to the connected `SYNC_CHANNEL_ID`, it is automatically inserted into the database via the `@dp.channel_post()` handler.
- **SQLite Tracker:** Uses an asynchronous SQLite library (`aiosqlite`) ensuring no blocking operations within event loops. Contains robust tracking for users and movies.
