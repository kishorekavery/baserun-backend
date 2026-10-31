"""
Profile photo pipeline. The client already crops and compresses, but nothing it
sends is trusted: every upload is size-checked, sniffed, decoded and re-encoded,
which also drops all metadata (camera EXIF can hold the runner's home GPS).

Photos live in PostgreSQL because Vercel functions have no persistent disk and
PostgreSQL is this project's only datastore. A 512 px JPEG is roughly 50-100 KB,
and the CDN serves repeat reads (see CACHE_CONTROL).
"""
import asyncio
import io
import os
import uuid
from typing import Optional

from fastapi import HTTPException, Request
from PIL import Image, ImageOps
from starlette.datastructures import UploadFile

from db.database import connection, transaction

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BODY_BYTES = MAX_FILE_BYTES + 64 * 1024  # room for the multipart framing
MAX_PIXELS = 25_000_000
AVATAR_SIZE = 512
JPEG_QUALITY = 82
MAX_UPLOADS_PER_HOUR = 10
# Immutable because each upload gets a new key. The CDN keeps a copy for a day
# only, so a removed photo or deleted account stops being served soon after.
CACHE_CONTROL = "public, max-age=31536000, s-maxage=86400, immutable"


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail="Photo must be 5 MB or smaller")


async def read_upload(request: Request) -> bytes:
    """Returns the bytes of the multipart `file` field, stopping as soon as the body exceeds the limit."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()

    received = 0

    async def limited_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_BODY_BYTES:  # chunked uploads declare no length
            raise _too_large()
        return message

    async with Request(request.scope, limited_receive).form(max_files=1, max_fields=1) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail="Send the photo as a multipart field named 'file'")
        data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise _too_large()
    return data


def sniff_image_type(data: bytes) -> Optional[str]:
    """Pillow format name from the magic bytes; the filename and header are attacker-controlled."""
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def normalize_image(data: bytes, kind: str) -> bytes:
    """Upright, center-cropped, white-backed AVATAR_SIZE JPEG with no metadata."""
    try:
        with Image.open(io.BytesIO(data), formats=(kind,)) as image:
            # Checked before decoding: a tiny file can expand to gigabytes of pixels
            if image.width * image.height > MAX_PIXELS:
                raise HTTPException(status_code=400, detail="Photo dimensions are too large")
            upright = ImageOps.exif_transpose(image).convert("RGBA")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="That file couldn't be read as an image")

    flattened = Image.new("RGBA", upright.size, "white")
    flattened.alpha_composite(upright)
    square = ImageOps.fit(flattened.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    square.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue()


def public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


async def upload_avatar(request: Request, user_id: int) -> None:
    data = await read_upload(request)
    kind = sniff_image_type(data)
    if kind is None:
        raise HTTPException(status_code=415, detail="Photo must be a JPEG, PNG or WebP image")

    async with connection() as conn:
        recent = await conn.fetchval(
            "SELECT count(*) FROM avatar_uploads WHERE user_id = $1 AND created_at > NOW() - INTERVAL '1 hour'",
            user_id,
        )
    if recent >= MAX_UPLOADS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many photo uploads, please try again later")

    jpeg = await asyncio.to_thread(normalize_image, data, kind)  # CPU bound
    key = uuid.uuid4().hex
    url = f"{public_base_url(request)}/api/v1/avatars/{key}.jpg"
    async with transaction() as conn:
        await conn.execute(
            """
            INSERT INTO avatars (user_id, key, data) VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE
            SET key = EXCLUDED.key, data = EXCLUDED.data, created_at = NOW()
            """,
            user_id, key, jpeg,
        )
        await conn.execute("UPDATE users SET avatar_url = $2 WHERE id = $1", user_id, url)
        await conn.execute("INSERT INTO avatar_uploads (user_id) VALUES ($1)", user_id)
        await conn.execute(
            "DELETE FROM avatar_uploads WHERE user_id = $1 AND created_at < NOW() - INTERVAL '1 hour'", user_id
        )


async def remove_avatar(user_id: int) -> None:
    async with transaction() as conn:
        await conn.execute("DELETE FROM avatars WHERE user_id = $1", user_id)
        await conn.execute("UPDATE users SET avatar_url = NULL WHERE id = $1", user_id)


async def get_avatar_image(key: str) -> Optional[bytes]:
    async with connection() as conn:
        return await conn.fetchval("SELECT data FROM avatars WHERE key = $1", key)
