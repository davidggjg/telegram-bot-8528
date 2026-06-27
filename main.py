"""
Telegram Stream-on-Demand Server
Pyrogram User-bot + FastAPI with Range header support (HTTP 206)
"""

import os
import asyncio
import logging
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait
import uvicorn

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

# ── Environment variables ─────────────────────────────────────────────────────
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ.get("SESSION_STRING", "")  # preferred for cloud
ALLOWED_CHAT_IDS_RAW = os.environ.get("ALLOWED_CHAT_IDS", "")  # comma-separated
PORT = int(os.environ.get("PORT", 8000))

ALLOWED_CHAT_IDS: set[int] = set()
if ALLOWED_CHAT_IDS_RAW:
    for part in ALLOWED_CHAT_IDS_RAW.split(","):
        part = part.strip()
        if part:
            ALLOWED_CHAT_IDS.add(int(part))

CHUNK_SIZE = 1024 * 512  # 512 KB per chunk

# ── Pyrogram client ───────────────────────────────────────────────────────────
if SESSION_STRING:
    app_client = Client(
        name="stream_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
    )
else:
    # Falls back to a local session file (useful for local development)
    app_client = Client(
        name="stream_bot",
        api_id=API_ID,
        api_hash=API_HASH,
    )

# ── FastAPI ───────────────────────────────────────────────────────────────────
api = FastAPI(title="Telegram Stream Server", version="1.0.0")


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Return (start, end) byte positions from an HTTP Range header."""
    try:
        unit, ranges = range_header.split("=")
        if unit.strip() != "bytes":
            raise ValueError("Only byte ranges supported")
        start_str, end_str = ranges.split("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)
        return start, end
    except Exception:
        raise HTTPException(status_code=416, detail="Invalid Range header")


async def get_message(chat_id: int, message_id: int) -> Message:
    """Fetch a single Telegram message, retrying on FloodWait."""
    for attempt in range(5):
        try:
            msg = await app_client.get_messages(chat_id, message_id)
            return msg
        except FloodWait as e:
            log.warning("FloodWait %s seconds (attempt %s)", e.value, attempt + 1)
            await asyncio.sleep(e.value)
    raise HTTPException(status_code=429, detail="Telegram rate limit hit")


async def stream_file(
    chat_id: int,
    message_id: int,
    start: int = 0,
    end: Optional[int] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Stream bytes from a Telegram media message directly to the HTTP response.
    Pipes data chunk-by-chunk without touching disk.
    """
    msg = await get_message(chat_id, message_id)

    if not msg or not msg.media:
        raise HTTPException(status_code=404, detail="No media found in message")

    media = msg.audio or msg.video or msg.document or msg.video_note
    if not media:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    # Pyrogram's stream_media yields chunks; we seek by skipping bytes.
    bytes_to_skip = start
    bytes_to_send = (end - start + 1) if end is not None else None
    sent = 0

    async for chunk in app_client.stream_media(msg, chunk_size=CHUNK_SIZE):
        if bytes_to_skip > 0:
            if bytes_to_skip >= len(chunk):
                bytes_to_skip -= len(chunk)
                continue
            chunk = chunk[bytes_to_skip:]
            bytes_to_skip = 0

        if bytes_to_send is not None:
            remaining = bytes_to_send - sent
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunk = chunk[:remaining]

        yield chunk
        sent += len(chunk)

        if bytes_to_send is not None and sent >= bytes_to_send:
            break


# ── Routes ────────────────────────────────────────────────────────────────────

@api.get("/ping")
async def ping():
    """Keep-alive / health check endpoint."""
    return JSONResponse({"status": "ok", "message": "pong"})


@api.get("/stream/{chat_id}/{message_id}")
async def stream(chat_id: int, message_id: int, request: Request):
    """
    Stream Telegram media with full Range support (HTTP 206).
    Supports seeking in video players.
    """
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        raise HTTPException(status_code=403, detail="Chat ID not allowed")

    # Fetch message metadata first to get file size
    msg = await get_message(chat_id, message_id)
    if not msg or not msg.media:
        raise HTTPException(status_code=404, detail="No media found")

    media = msg.audio or msg.video or msg.document or msg.video_note
    if not media:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    file_size: int = media.file_size
    mime_type: str = getattr(media, "mime_type", "application/octet-stream")
    file_name: str = getattr(media, "file_name", f"media_{message_id}")

    range_header = request.headers.get("Range")

    if range_header:
        start, end = parse_range_header(range_header, file_size)
        content_length = end - start + 1

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'inline; filename="{file_name}"',
        }
        return StreamingResponse(
            stream_file(chat_id, message_id, start, end),
            status_code=206,
            media_type=mime_type,
            headers=headers,
        )

    # Full file (no Range header)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Disposition": f'inline; filename="{file_name}"',
    }
    return StreamingResponse(
        stream_file(chat_id, message_id, 0),
        status_code=200,
        media_type=mime_type,
        headers=headers,
    )


@api.get("/info/{chat_id}/{message_id}")
async def media_info(chat_id: int, message_id: int):
    """Return metadata about a Telegram media message."""
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        raise HTTPException(status_code=403, detail="Chat ID not allowed")

    msg = await get_message(chat_id, message_id)
    if not msg or not msg.media:
        raise HTTPException(status_code=404, detail="No media found")

    media = msg.audio or msg.video or msg.document or msg.video_note
    if not media:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    return {
        "file_size": media.file_size,
        "mime_type": getattr(media, "mime_type", None),
        "file_name": getattr(media, "file_name", None),
        "duration": getattr(media, "duration", None),
        "width": getattr(media, "width", None),
        "height": getattr(media, "height", None),
    }


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@api.on_event("startup")
async def startup():
    log.info("Starting Pyrogram client…")
    await app_client.start()
    log.info("Pyrogram client ready. Allowed chats: %s", ALLOWED_CHAT_IDS or "ALL")


@api.on_event("shutdown")
async def shutdown():
    log.info("Stopping Pyrogram client…")
    await app_client.stop()


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:api",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
