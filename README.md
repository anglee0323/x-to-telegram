# x-to-telegram

<p align="center">
  <b>Seamlessly synchronize X (Twitter) posts to Telegram channels with media, quotes, and deletion tracking using the official Twitter API v2.</b>
</p>

<p align="center">
  <a href="README.md"><b>English</b></a> | <a href="README_CN.md"><b>简体中文</b></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/uv-supported-purple.svg" alt="uv Supported" />
  <img src="https://img.shields.io/badge/Twitter%20API-v2-1DA1F2.svg" alt="Twitter API v2" />
  <img src="https://img.shields.io/badge/Telegram-Channel%20Sync-0088cc.svg" alt="Telegram" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License" />
</p>

---

## Highlights

- **Official Twitter API v2**: Uses official Twitter Bearer Token authentication (`https://api.twitter.com/2`), supporting Note Tweets (long-form posts), attachments, and referenced tweets.
- **Complete Media Support**: Automatically handles single photos, multi-image albums (`sendMediaGroup`), animated GIFs, and videos with highest available bitrate MP4 extraction.
- **Quote & Threading Context**: Quoting a previously synced tweet sends a native Telegram reply (`reply_to_message_id`). Quoting an external tweet appends an attribution link.
- **Bi-directional Deletion Sync**: Detects tweets deleted on X within the last N days (configurable) and automatically removes corresponding Telegram messages.
- **Flexible Trigger Mode**:
  - Filter by a specific hashtag (e.g., `#SyncToTelegram`).
  - Or leave `SYNC_HASHTAG=` empty to automatically sync **all** tweets.
- **URL & Entity Cleanup**: Automatically resolves `t.co` shortlinks to real destination URLs while cleaning up trailing media URLs.
- **Long Text Protection**: Respects Telegram's 1024-character caption limit for media, gracefully sending remaining content as a threaded reply so messages never drop.
- **Zero-Dependency Runtime**: Ready to run out of the box with `uv run main.py` using PEP 723 inline script metadata, or with standard `python -m venv` / `pip install -r requirements.txt`.
- **Flexible Deployment**: Ready for Linux `systemd` daemon, `cron`, or GitHub Actions.

---

## How It Works

```text
       ┌─────────────────┐
       │   X (Twitter)   │
       └────────┬────────┘
                │ Twitter API v2 (Bearer Token)
                ▼
       ┌─────────────────┐
       │  x-to-telegram  │◄── state.json (incremental cursor & message map)
       └────────┬────────┘
                │
     ┌──────────┴──────────┐
     ▼                     ▼
[New Tweet]          [Deleted Tweet]
     │                     │
     ▼                     ▼
Telegram Channel      Telegram Channel
(Send Post/Media)     (Delete Message)
```

1. **State Loading**: Reads `state.json` to obtain the incremental sync cursor (`last_tweet_id`) and message mapping table (`msg_map`).
2. **Deletion Check**: Scans tweets synced within the configured window (`DELETE_CHECK_DAYS`, default 7 days). If a tweet was deleted on X, deletes the corresponding Telegram message.
3. **Fetch New Tweets**: Fetches latest user tweets using pagination cursor and snowflake ID comparison, ignoring retweets and replies.
4. **Chronological Publishing**: Sorts tweets chronologically and pushes each matching post to Telegram, formatting media and replies.
5. **Atomic Persistence**: Updates `last_tweet_id` and `msg_map`, atomically saving to `state.json`.

---

## Quick Start

### 1. Prerequisites

- **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) (recommended) or standard `pip`.
- **Twitter Developer Account**: Obtain a Bearer Token from the [Twitter Developer Portal](https://developer.x.com).
- **Telegram Bot**:
  1. Create a bot using [@BotFather](https://t.me/BotFather) and obtain the `TELEGRAM_BOT_TOKEN`.
  2. Create a Telegram Channel, add your bot as an **Administrator** with *Post Messages* and *Delete Messages* permissions.
  3. Obtain the Channel ID or username (e.g., `@your_channel` or `-1001234567890`).

### 2. Clone & Setup

```bash
git clone https://github.com/anglee0323/x-to-telegram.git
cd x-to-telegram

cp .env.example .env
```

Edit `.env` with your credentials:

```ini
# Required
TWITTER_BEARER_TOKEN=your_twitter_bearer_token_here
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHANNEL_ID=@your_channel_username
TWITTER_USERNAME=your_twitter_username

# Optional
SYNC_HASHTAG=           # Leave empty to sync all tweets, or specify a hashtag like SyncToTelegram
DELETE_CHECK_DAYS=7
```

### 3. Run

**Using `uv` (Recommended - No manual venv/pip needed):**
```bash
uv run main.py
```

**Using standard Python & pip:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Configuration Reference

All settings can be configured via environment variables or `.env`:

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `TWITTER_BEARER_TOKEN` | **Yes** | — | Twitter / X API v2 Bearer Token |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | Telegram Bot API token from @BotFather |
| `TELEGRAM_CHANNEL_ID` | **Yes** | — | Target channel ID (e.g. `-100...`) or `@channel` |
| `TWITTER_USERNAME` | **Yes** | — | Twitter / X username to monitor (without `@`) |
| `SYNC_HASHTAG` | No | *(empty)* | Only sync tweets containing this hashtag. Leave empty to sync **all** tweets |
| `TWITTER_API_BASE` | No | `https://api.twitter.com/2` | Twitter API v2 base endpoint |
| `DELETE_CHECK_DAYS` | No | `7` | Check deletion status of tweets synced within N days |
| `STATE_FILE` | No | `state.json` | Path to sync state file |
| `REQUEST_TIMEOUT` | No | `30` | HTTP request timeout in seconds |
| `MAX_HISTORY_DAYS` | No | `60` | Prune message mapping history older than N days |

---

## Deployment Options

### Option A: Linux `systemd` Service (Recommended for servers)

Create `/etc/systemd/system/x-to-telegram.service`:

```ini
[Unit]
Description=X to Telegram Sync Service
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/x-to-telegram
ExecStart=/path/to/uv run main.py
Restart=always
RestartSec=60
EnvironmentFile=/path/to/x-to-telegram/.env

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now x-to-telegram
sudo systemctl status x-to-telegram
```

### Option B: Crontab

To run every 5 minutes via crontab:

```bash
*/5 * * * * cd /path/to/x-to-telegram && uv run main.py >> /var/log/x-to-telegram.log 2>&1
```

### Option C: GitHub Actions

You can use the included [`.github/workflows/sync.yml`](.github/workflows/sync.yml):

1. Go to your repository **Settings** → **Secrets and variables** → **Actions**.
2. Add the following repository secrets:
   - `TWITTER_BEARER_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`
   - `TWITTER_USERNAME`
3. In `.github/workflows/sync.yml`, uncomment the `schedule` trigger to run automatically on a recurring schedule.

---

## License

This project is licensed under the [MIT License](LICENSE).
