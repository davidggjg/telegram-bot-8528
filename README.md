# 📡 Telegram Stream-on-Demand Server

A production-ready User-bot that streams Telegram media directly to any video player — with full **HTTP 206 Range** support for seeking, no local storage, and 24/7 cloud deployment.

---

## 🚀 Features

| Feature | Details |
|---|---|
| **HTTP 206 Partial Content** | Full seeking/scrubbing in any player |
| **No local storage** | Data piped directly Telegram → client |
| **Bypasses 20 MB limit** | User-bot session has no file-size cap |
| **Chat whitelist** | Only approved `ALLOWED_CHAT_IDS` are served |
| **Keep-alive endpoint** | `/ping` for uptime monitors (e.g. UptimeRobot) |
| **Media metadata** | `/info/{chat_id}/{message_id}` endpoint |

---

## 🔧 Environment Variables

Set these in Render (or any cloud provider) → **Environment** tab:

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | Your Telegram `api_id` from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | Your Telegram `api_hash` from [my.telegram.org](https://my.telegram.org) |
| `SESSION_STRING` | ✅ (cloud) | Pyrogram session string (see below) |
| `ALLOWED_CHAT_IDS` | ⚠️ Recommended | Comma-separated list of allowed chat IDs, e.g. `-1001234567890,-1009876543210` |
| `PORT` | Optional | HTTP port (Render sets this automatically) |

---

## 🔑 Generating a SESSION_STRING

Run this **once on your local machine** (Python 3.11+):

```bash
pip install pyrogram TgCrypto
python -c "
from pyrogram import Client
import asyncio

async def gen():
    async with Client(':memory:', api_id=YOUR_API_ID, api_hash='YOUR_API_HASH') as c:
        print(await c.export_session_string())

asyncio.run(gen())
"
```

Copy the printed string and paste it as the `SESSION_STRING` environment variable on Render.

---

## ☁️ Deploy to Render

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com) → **New → Web Service**.
3. Connect your GitHub repo.
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `uvicorn main:api --host 0.0.0.0 --port $PORT`
6. Add all environment variables from the table above.
7. Click **Deploy**.

> **Keep-alive tip:** Add `https://your-app.onrender.com/ping` to [UptimeRobot](https://uptimerobot.com) with a 5-minute interval so Render's free tier never sleeps.

---

## 🎬 Streaming URLs

Once deployed, use these URLs in any player (VLC, browser `<video>` tag, etc.):

```
# Stream media (supports Range / seeking)
GET https://your-app.onrender.com/stream/{chat_id}/{message_id}

# Get media metadata (size, duration, mime type)
GET https://your-app.onrender.com/info/{chat_id}/{message_id}

# Health check
GET https://your-app.onrender.com/ping
```

### Example — HTML5 player

```html
<video controls>
  <source src="https://your-app.onrender.com/stream/-1001234567890/42" type="video/mp4">
</video>
```

---

## 🔍 Finding chat_id and message_id

- **chat_id**: Forward a message from the target channel to [@userinfobot](https://t.me/userinfobot) or use `get_chat()` in Pyrogram.
- **message_id**: The number in the message link — `t.me/c/1234567890/**42**`.

---

## 📦 Local Development

```bash
# Clone & install
pip install -r requirements.txt

# Set env vars
export API_ID=12345678
export API_HASH=abcdef1234567890abcdef1234567890
export ALLOWED_CHAT_IDS=-1001234567890

# Run (will prompt for phone number on first run, creating a local session file)
python main.py
```

---

## ⚠️ Disclaimer

This project uses a **User-bot** (your personal Telegram account). Use responsibly and only with content you have rights to access. Violating Telegram's ToS may result in account restrictions.
