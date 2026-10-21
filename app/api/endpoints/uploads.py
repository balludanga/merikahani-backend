"""Media uploads proxied to Supabase Storage.

Render's free tier has no persistent disk, so the browser compresses the
image and posts base64 here; we forward the bytes to Supabase Storage with
the service key (never exposed to the frontend) and return the public URL.
"""

import base64
import binascii
import uuid

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.models.user import User

router = APIRouter()

# Compressed editor uploads are ~100-300 KB; 2 MB is a generous ceiling.
MAX_IMAGE_BYTES = 2 * 1024 * 1024

# mime -> file extension in the bucket
ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


class ImagePayload(BaseModel):
    data: str            # data: URL or raw base64
    content_type: str    # must be one of ALLOWED_TYPES


def _decode(data: str, content_type: str) -> bytes:
    raw = data.split("base64,", 1)[1] if data.startswith("data:") else data
    try:
        binary = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="चित्र डेटा पढ़ा नहीं जा सका।")
    if not binary:
        raise HTTPException(status_code=400, detail="चित्र खाली है।")
    if len(binary) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="चित्र बहुत बड़ा है — पहले संपादक में उसे छोटा होने दें।",
        )
    # Magic-number sniff: refuse anything that isn't really the claimed type.
    head = binary[:12]
    magic_ok = {
        "image/jpeg": head.startswith(b"\xff\xd8\xff"),
        "image/png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        "image/gif": head[:6] in (b"GIF87a", b"GIF89a"),
    }[content_type]
    if not magic_ok:
        raise HTTPException(status_code=400, detail="फ़ाइल का असली रूप बताए गए प्रकार से मेल नहीं खाता।")
    return binary


@router.post("/image")
def upload_image(
    payload: ImagePayload,
    db_user: User = Depends(get_current_user),
):
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=503,
            detail="अपलोड सेवा अभी चालू नहीं है — चित्र की कड़ी से जोड़ें।",
        )
    ext = ALLOWED_TYPES.get(payload.content_type)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="सिर्फ़ JPEG, PNG, WebP या GIF चित्र ही जोड़े जा सकते हैं।",
        )
    binary = _decode(payload.data, payload.content_type)

    path = f"media/{db_user.id}/{uuid.uuid4().hex}.{ext}"
    base = settings.SUPABASE_URL.rstrip("/")
    upload_url = f"{base}/storage/v1/object/{settings.UPLOAD_BUCKET}/{path}"
    try:
        response = requests.post(
            upload_url,
            data=binary,
            headers={
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": payload.content_type,
                "x-upsert": "false",
            },
            timeout=30,
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="अपलोड सेवा से संपर्क नहीं हो पाया।")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="चित्र अपलोड नहीं हो पाया। दोबारा कोशिश करें।")

    public_url = f"{base}/storage/v1/object/public/{settings.UPLOAD_BUCKET}/{path}"
    return {"url": public_url, "bytes": len(binary)}
