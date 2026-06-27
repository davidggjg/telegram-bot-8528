"""
Telegram Stream-on-Demand Server
- Pyrogram User-bot streams from "Saved Messages"
- Bot receives files, forwards to Saved Messages, returns stream link
- FastAPI with HTTP 206 Range support
- Built-in keep-alive every 5 minutes
- Web dashboard
"""

import os
import asyncio
import logging
import httpx
from typing import AsyncGenerator, Optional
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
import uvicorn

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ── Environment Variables ─────────────────────────────────────────────────────
API_ID         = int(os.environ["API_ID"])
API_HASH       = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
BOT_TOKEN      = os.environ["BOT_TOKEN"]
PORT           = int(os.environ.get("PORT", 8000))
BASE_URL       = os.environ.get("BASE_URL", f"http://localhost:{PORT}")

CHUNK_SIZE = 1024 * 512  # 512 KB

# ── Stats ─────────────────────────────────────────────────────────────────────
stats = {
    "started_at": datetime.utcnow().isoformat(),
    "files_processed": 0,
    "links_generated": 0,
    "last_file": None,
    "last_ping": None,
}

# ── Pyrogram User-bot ─────────────────────────────────────────────────────────
user_client = Client(
    name="stream_userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

# ── Pyrogram Bot ──────────────────────────────────────────────────────────────
bot_client = Client(
    name="stream_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# ── FastAPI ───────────────────────────────────────────────────────────────────
api = FastAPI(title="Telegram Stream Server")

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    try:
        _, ranges = range_header.split("=")
        start_str, end_str = ranges.split("-")
        start = int(start_str) if start_str else 0
        end   = int(end_str)   if end_str   else file_size - 1
        return start, min(end, file_size - 1)
    except Exception:
        raise HTTPException(status_code=416, detail="Invalid Range header")


async def fetch_message(chat_id: int, message_id: int) -> Message:
    for attempt in range(5):
        try:
            return await user_client.get_messages(chat_id, message_id)
        except FloodWait as e:
            log.warning("FloodWait %ss (attempt %s)", e.value, attempt + 1)
            await asyncio.sleep(e.value)
    raise HTTPException(status_code=429, detail="Telegram rate limit")


async def stream_chunks(
    chat_id: int,
    message_id: int,
    start: int = 0,
    end: Optional[int] = None,
) -> AsyncGenerator[bytes, None]:
    msg = await fetch_message(chat_id, message_id)
    if not msg or not msg.media:
        raise HTTPException(status_code=404, detail="No media in message")

    media = msg.audio or msg.video or msg.document or msg.video_note
    if not media:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    skip    = start
    to_send = (end - start + 1) if end is not None else None
    sent    = 0

    async for chunk in user_client.stream_media(msg, chunk_size=CHUNK_SIZE):
        if skip > 0:
            if skip >= len(chunk):
                skip -= len(chunk)
                continue
            chunk = chunk[skip:]
            skip = 0

        if to_send is not None:
            remaining = to_send - sent
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunk = chunk[:remaining]

        yield chunk
        sent += len(chunk)
        if to_send is not None and sent >= to_send:
            break

# ── Stream Route ──────────────────────────────────────────────────────────────

@api.get("/stream/{chat_id}/{message_id}")
async def stream(chat_id: int, message_id: int, request: Request):
    msg = await fetch_message(chat_id, message_id)
    if not msg or not msg.media:
        raise HTTPException(status_code=404, detail="No media found")

    media     = msg.audio or msg.video or msg.document or msg.video_note
    if not media:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    file_size = media.file_size
    mime_type = getattr(media, "mime_type", "application/octet-stream")
    file_name = getattr(media, "file_name", f"file_{message_id}")

    range_header = request.headers.get("Range")
    if range_header:
        start, end = parse_range(range_header, file_size)
        headers = {
            "Content-Range":       f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges":       "bytes",
            "Content-Length":      str(end - start + 1),
            "Content-Disposition": f'inline; filename="{file_name}"',
        }
        return StreamingResponse(
            stream_chunks(chat_id, message_id, start, end),
            status_code=206, media_type=mime_type, headers=headers,
        )

    headers = {
        "Accept-Ranges":       "bytes",
        "Content-Length":      str(file_size),
        "Content-Disposition": f'inline; filename="{file_name}"',
    }
    return StreamingResponse(
        stream_chunks(chat_id, message_id),
        status_code=200, media_type=mime_type, headers=headers,
    )

# ── Ping ──────────────────────────────────────────────────────────────────────

@api.get("/ping")
async def ping():
    stats["last_ping"] = datetime.utcnow().isoformat()
    return JSONResponse({"status": "ok"})

# ── Dashboard ─────────────────────────────────────────────────────────────────

@api.get("/", response_class=HTMLResponse)
async def dashboard():
    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Telegram Stream Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', sans-serif; background: #0f0f0f; color: #e0e0e0; min-height: 100vh; padding: 24px 16px; }}
    h1 {{ font-size: 1.6rem; color: #fff; margin-bottom: 6px; }}
    .subtitle {{ color: #888; font-size: 0.9rem; margin-bottom: 28px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 28px; }}
    .card {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; padding: 20px 16px; text-align: center; }}
    .card .num {{ font-size: 2rem; font-weight: 700; color: #4f9eff; }}
    .card .label {{ font-size: 0.8rem; color: #888; margin-top: 6px; }}
    .section {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
    .section h2 {{ font-size: 1rem; color: #aaa; margin-bottom: 14px; border-bottom: 1px solid #2a2a2a; padding-bottom: 10px; }}
    .row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #222; font-size: 0.88rem; }}
    .row:last-child {{ border-bottom: none; }}
    .row .key {{ color: #888; }}
    .row .val {{ color: #ddd; word-break: break-all; text-align: left; max-width: 65%; }}
    .status-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #22c55e; margin-left: 8px; animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
    .how {{ background: #111; border: 1px solid #2a2a2a; border-radius: 8px; padding: 14px 16px; font-size: 0.82rem; color: #aaa; line-height: 1.8; }}
    .how code {{ background: #222; padding: 2px 6px; border-radius: 4px; color: #4f9eff; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>📡 Telegram Stream Server <span class="status-dot"></span></h1>
  <p class="subtitle">User-bot streaming — עוקף מגבלת 20MB</p>
  <div class="grid">
    <div class="card"><div class="num">{stats['files_processed']}</div><div class="label">קבצים שהתקבלו</div></div>
    <div class="card"><div class="num">{stats['links_generated']}</div><div class="label">קישורים שנוצרו</div></div>
    <div class="card"><div class="num" style="font-size:1rem;margin-top:8px">{stats['started_at'][:10]}</div><div class="label">פעיל מאז</div></div>
  </div>
  <div class="section">
    <h2>📊 מידע נוסף</h2>
    <div class="row"><span class="key">קובץ אחרון</span><span class="val">{stats['last_file'] or '—'}</span></div>
    <div class="row"><span class="key">פינג אחרון</span><span class="val">{stats['last_ping'] or '—'}</span></div>
    <div class="row"><span class="key">Base URL</span><span class="val">{BASE_URL}</span></div>
  </div>
  <div class="section">
    <h2>🎬 איך משתמשים?</h2>
    <div class="how">
      1. שלח לבוט קובץ וידאו / אודיו<br>
      2. הבוט מעביר אוטומטית ל-Saved Messages של היוזר-בוט<br>
      3. מקבל קישור סטרימינג מיידי עם Seek מלא ✅<br><br>
      <strong>פורמט URL:</strong><br>
      <code>{BASE_URL}/stream/CHAT_ID/MESSAGE_ID</code>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)

# ── Bot handlers ──────────────────────────────────────────────────────────────

@bot_client.on_message(filters.private & (filters.video | filters.audio | filters.document | filters.video_note))
async def handle_media(client, message: Message):
    stats["files_processed"] += 1

    media     = message.video or message.audio or message.document or message.video_note
    file_name = getattr(media, "file_name", "קובץ")
    file_size = getattr(media, "file_size", 0)
    size_mb   = round(file_size / 1024 / 1024, 1)

    # שלח הודעת המתנה
    wait_msg = await message.reply_text("⏳ מעבד את הקובץ...")

    try:
        # העבר את הקובץ ל-Saved Messages של היוזר-בוט
        forwarded = await user_client.forward_messages(
            chat_id="me",
            from_chat_id=message.chat.id,
            message_ids=message.id,
        )

        # הקישור מבוסס על Saved Messages (chat_id = "me")
        me = await user_client.get_me()
        saved_chat_id = me.id
        stream_url = f"{BASE_URL}/stream/{saved_chat_id}/{forwarded.id}"

        stats["links_generated"] += 1
        stats["last_file"] = f"{file_name} ({size_mb}MB)"

        await wait_msg.edit_text(
            f"✅ **קישור סטרימינג מוכן!**\n\n"
            f"📄 קובץ: `{file_name}`\n"
            f"📦 גודל: {size_mb} MB\n\n"
            f"🔗 **קישור:**\n`{stream_url}`\n\n"
            f"_הקישור תומך ב-Seek מלא ועובד בכל נגן_ 🎬"
        )
        log.info("Stream link generated: %s", stream_url)

    except Exception as e:
        log.error("Error forwarding: %s", e)
        await wait_msg.edit_text(f"❌ שגיאה: {str(e)}")


@bot_client.on_message(filters.private & filters.command("start"))
async def start_command(client, message: Message):
    await message.reply_text(
        "👋 **שלום!**\n\n"
        "שלח לי קובץ וידאו או אודיו ואני אחזיר לך קישור סטרימינג מיידי.\n\n"
        "הקישור עובד בכל נגן ותומך בהזזת הסרגל (Seek) ✅"
    )

# ── Keep-alive ────────────────────────────────────────────────────────────────

async def keep_alive():
    await asyncio.sleep(30)
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{BASE_URL}/ping", timeout=10)
                log.info("Keep-alive ping sent")
        except Exception as e:
            log.warning("Keep-alive failed: %s", e)
        await asyncio.sleep(300)

# ── Lifecycle ─────────────────────────────────────────────────────────────────

@api.on_event("startup")
async def startup():
    log.info("Starting User-bot...")
    await user_client.start()
    log.info("Starting Bot...")
    await bot_client.start()
    asyncio.create_task(keep_alive())
    log.info("All systems ready ✅")


@api.on_event("shutdown")
async def shutdown():
    await user_client.stop()
    await bot_client.stop()

# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:api", host="0.0.0.0", port=PORT, log_level="info")
