"""
Telegram Stream-on-Demand Server
ארכיטקטורה מפושטת: בוט אחד, מחובר ב-MTProto (לא Bot API HTTP), שמזרים
ישירות מהצ'אט המקורי שבו הוא קיבל את הקובץ. אין userbot, אין
SESSION_STRING, אין copy/forward ל-Saved Messages.

למה זה עובד בלי מגבלת 20MB?
ה-20MB הוא מגבלה של שכבת ה-HTTP Bot API (api.telegram.org/bot.../getFile)
בלבד. Pyrogram מדבר ישירות עם שרתי MTProto של טלגרם — אותו פרוטוקול
שאפליקציית טלגרם הרגילה משתמשת בו — ולכן לא כפוף למגבלה הזו. בוט
שמחובר עם bot_token דרך Pyrogram יכול להוריד/להזרים קבצים גדולים בלי
שום תחבולה.

זה מבטל לגמרי את הבאג "'NoneType' object has no attribute 'id'" כי
אין יותר שום קופי/פורוורד — המסר נשלף ישירות מהמיקום המקורי שלו.
"""

import os
import sys
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ── בדיקת משתני סביבה ──────────────────────────────────────────────────────
# שימי לב: SESSION_STRING לא נדרש יותר! רק 3 ערכים.
REQUIRED_ENV_VARS = ["API_ID", "API_HASH", "BOT_TOKEN"]
_missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
if _missing:
    sys.exit(
        f"❌ חסרים משתני סביבה: {', '.join(_missing)}\n"
        f"   הגדר אותם ב-Render → Environment ונסה שוב."
    )

API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT      = int(os.environ.get("PORT", 8000))

# Render מגדיר את זה אוטומטית לכתובת הציבורית האמיתית של השירות.
BASE_URL = (
    os.environ.get("BASE_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or f"http://localhost:{PORT}"
).rstrip("/")

stats = {
    "started_at": datetime.utcnow().isoformat(),
    "files_processed": 0,
    "links_generated": 0,
    "last_file": None,
    "last_ping": None,
}

# בוט אחד בלבד, מחובר ב-MTProto (לא Bot API HTTP) — גם מקבל הודעות וגם מזרים מהן.
bot_client = Client(
    name="stream_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

api = FastAPI(title="Telegram Stream Server")

# ── Stream helpers ────────────────────────────────────────────────────────────

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
            msg = await bot_client.get_messages(chat_id, message_id)
            return msg
        except FloodWait as e:
            log.warning("FloodWait %ss", e.value)
            await asyncio.sleep(e.value)
    raise HTTPException(status_code=429, detail="Rate limit")


PYROGRAM_CHUNK_SIZE = 1024 * 1024  # Pyrogram's chunk size is fixed at 1 MiB — not configurable


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

    # stream_media() אין לו chunk_size — הוא קבוע על 1MiB. כדי לדלג נכון
    # (Range/Seek) משתמשים ב-offset (איזה chunk להתחיל ממנו, ביחידות של
    # 1MiB) ו-limit (כמה chunks לשלוף). זה גם הרבה יותר יעיל: טלגרם
    # מתחיל לשלוח מהמקום הנכון בקובץ, ולא צריך להוריד את כל מה שלפניו.
    offset = start // PYROGRAM_CHUNK_SIZE
    skip   = start % PYROGRAM_CHUNK_SIZE

    if end is not None:
        last_chunk = end // PYROGRAM_CHUNK_SIZE
        limit = last_chunk - offset + 1
    else:
        limit = 0  # 0 = עד סוף הקובץ

    to_send = (end - start + 1) if end is not None else None
    sent    = 0

    async for chunk in bot_client.stream_media(msg, offset=offset, limit=limit):
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

# ── Routes ────────────────────────────────────────────────────────────────────

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


@api.get("/ping")
async def ping():
    stats["last_ping"] = datetime.utcnow().isoformat()
    return JSONResponse({"status": "ok"})


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
  <p class="subtitle">MTProto streaming — בוט יחיד, בלי 20MB limit</p>
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
      2. קבל קישור סטרימינג מיידי ✅<br>
      3. עובד בכל נגן עם Seek מלא, גם מעל 20MB 🎬<br><br>
      <strong>פורמט URL:</strong><br>
      <code>{BASE_URL}/stream/CHAT_ID/MESSAGE_ID</code>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)

# ── Bot handler ───────────────────────────────────────────────────────────────

@bot_client.on_message(filters.private & (filters.video | filters.audio | filters.document | filters.video_note))
async def handle_media(client, message: Message):
    stats["files_processed"] += 1
    wait_msg = await message.reply_text("⏳ מעבד...")

    try:
        media     = message.video or message.audio or message.document or message.video_note
        file_name = getattr(media, "file_name", "קובץ")
        file_size = getattr(media, "file_size", 0)
        size_mb   = round(file_size / 1024 / 1024, 1)

        # אין יותר copy/forward — מזרימים ישירות מההודעה המקורית
        # שבה הבוט עצמו קיבל את הקובץ (message.chat.id / message.id).
        stream_url = f"{BASE_URL}/stream/{message.chat.id}/{message.id}"

        stats["links_generated"] += 1
        stats["last_file"] = f"{file_name} ({size_mb}MB)"

        await wait_msg.edit_text(
            f"✅ **קישור סטרימינג מוכן!**\n\n"
            f"📄 קובץ: `{file_name}`\n"
            f"📦 גודל: {size_mb} MB\n\n"
            f"🔗 **קישור:**\n`{stream_url}`\n\n"
            f"_הקישור תומך ב-Seek מלא ועובד בכל נגן_ 🎬"
        )
        log.info("Stream link: %s", stream_url)

    except Exception as e:
        log.exception("Error handling media")
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
            async with httpx.AsyncClient() as c:
                await c.get(f"{BASE_URL}/ping", timeout=10)
                log.info("Keep-alive ✅")
        except Exception as e:
            log.warning("Keep-alive failed: %s", e)
        await asyncio.sleep(300)

# ── Lifecycle ─────────────────────────────────────────────────────────────────

@api.on_event("startup")
async def startup():
    await bot_client.start()
    asyncio.create_task(keep_alive())
    log.info("All systems ready ✅ BASE_URL=%s", BASE_URL)


@api.on_event("shutdown")
async def shutdown():
    await bot_client.stop()

if __name__ == "__main__":
    uvicorn.run("main:api", host="0.0.0.0", port=PORT, log_level="info")
