"""Narrated audio of a story ("▶ सुनें"), made on the server in a natural Hindi voice.

The first listen of a story asks Azure AI Speech (free tier) for the audio and keeps the MP3 in
the database; every later listen is a plain read. Anything that stops this (no key set, quota
used up, outage, very long story) answers with a status the page treats as "use the browser's
own voice instead", so listening never breaks.
"""

import hashlib
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.content import PREVIEW_LIMIT, can_view_post
from app.core.dependencies import get_current_user_optional
from app.core.narration import NARRATION_VERSION, plan_narration, spoken_length, to_ssml_chunks
from app.core.ratelimit import actions, client_ip, enforce
from app.core.speech import SpeechUnavailable, is_configured, synthesize
from app.database import get_db
from app.models.post import Post
from app.models.story_audio import StoryAudio, TtsUsage
from app.models.user import User

router = APIRouter()

# One synthesis per story at a time (a second listener waits for the first one's result), and
# at most two in flight overall so the small free server is never swamped.
_locks: dict = {}
_slots = threading.BoundedSemaphore(2)
# Fresh audio made per hour by one listener. Hearing a story that already has audio is free.
GENERATIONS_PER_HOUR = 10


def _fingerprint(post: Post) -> str:
    text = f"{NARRATION_VERSION}\n{settings.TTS_VOICE}\n{post.title or ''}\n{post.content or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audio_response(audio: bytes, digest: str, request: Request) -> Response:
    etag = f'"{digest[:32]}"'
    # private: the audio of a members-only story must never sit in a shared cache.
    headers = {"ETag": etag, "Cache-Control": "private, max-age=3600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=audio, media_type="audio/mpeg", headers=headers)


@router.get("/status")
def audio_status():
    """Whether the server can narrate at all, so the page picks its voice before the tap."""
    return {"enabled": is_configured()}


@router.get("/post/{post_id}")
def get_story_audio(
    post_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if not is_configured():
        raise HTTPException(status_code=503, detail="सर्वर की आवाज़ अभी चालू नहीं है")

    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    content = post.content or ""
    if not content.strip():
        raise HTTPException(status_code=404, detail="सुनाने के लिए कुछ नहीं है")
    # Same rule as reading: a signed-out visitor only gets the short, ungated stories.
    if current_user is None and len(content) > PREVIEW_LIMIT:
        raise HTTPException(status_code=401, detail="पूरी रचना सुनने के लिए साइन इन करें")
    if len(content) > settings.TTS_MAX_STORY_CHARS:
        raise HTTPException(status_code=413, detail="यह रचना सर्वर की आवाज़ के लिए बहुत लंबी है")

    digest = _fingerprint(post)
    row = db.query(StoryAudio).filter(StoryAudio.post_id == post_id).first()
    if row and row.content_hash == digest:
        return _audio_response(row.audio, digest, request)

    key = f"tts:{current_user.id}" if current_user else f"tts-ip:{client_ip(request)}"
    enforce(actions, key, GENERATIONS_PER_HOUR, 3600, record=False)

    with _locks.setdefault(post_id, threading.Lock()):
        db.expire_all()  # someone else may have finished while this request waited
        row = db.query(StoryAudio).filter(StoryAudio.post_id == post_id).first()
        if row and row.content_hash == digest:
            return _audio_response(row.audio, digest, request)

        segments = plan_narration(content, post.title or "")
        chars = spoken_length(segments)
        month = datetime.utcnow().strftime("%Y-%m")
        usage = db.query(TtsUsage).filter(TtsUsage.month == month).first()
        if (usage.chars if usage else 0) + chars > settings.TTS_MONTHLY_CHARS:
            raise HTTPException(status_code=503, detail="इस महीने की सर्वर-आवाज़ की सीमा पूरी हो चुकी है")

        chunks = to_ssml_chunks(segments, settings.TTS_VOICE)
        with _slots:
            try:
                audio = synthesize(chunks)
            except SpeechUnavailable as exc:
                raise HTTPException(status_code=503, detail="सर्वर की आवाज़ अभी उपलब्ध नहीं है") from exc
        actions.record(key)

        if row:
            row.content_hash, row.voice, row.audio, row.chars = digest, settings.TTS_VOICE, audio, chars
            row.created_at = datetime.utcnow()
        else:
            db.add(StoryAudio(post_id=post_id, content_hash=digest, voice=settings.TTS_VOICE, audio=audio, chars=chars))
        if usage:
            usage.chars += chars
        else:
            db.add(TtsUsage(month=month, chars=chars))
        db.commit()
        return _audio_response(audio, digest, request)
