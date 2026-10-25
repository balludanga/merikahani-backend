"""Narrated audio of a story ("▶ सुनें"), made on the server in a natural Hindi voice.

The first listen of a story starts a background job that asks Google's Gemini speech model for
the audio, joins the pieces into one MP3 and keeps it in the database. The page asks again every
few seconds: while the job runs the answer is 202 with its progress, then 200 with the audio.
Every later listen is a plain database read. Anything that stops this (no key, free quota used,
outage, an unpublished or very long story) answers with a status the page treats as "use the
browser's own voice instead", so listening never breaks.

Only PUBLISHED stories are sent to the speech service: on the free tier Google may use the text
to improve its products, which is fine for public writing but not for a private draft.
"""

import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.content import PREVIEW_LIMIT
from app.core.dependencies import get_current_user_optional
from app.core.casting import plan_story
from app.core.narration import DIALOGUE_STYLE, STYLE, fingerprint, spoken_chars
from app.core.ratelimit import actions, client_ip, enforce
from app.core.speech import SpeechUnavailable, is_available, synthesize
from app.database import SessionLocal, get_db
from app.models.post import Post
from app.models.story_audio import StoryAudio, TtsUsage
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

# Fresh narrations one listener may start per hour. Hearing a story that already has audio is free.
GENERATIONS_PER_HOUR = 10
# A failed job is remembered this long so every waiting tab sees the failure, then forgotten.
FAILURE_MEMORY = 60


class Partial:
    """A story's plan and the audio finished so far, kept in memory.

    The free speech allowance can run out half way through a long story. The retry (another
    model, or the next day) must not pay for the finished part again, and must reuse the same
    plan, because the cast is read by a model and could come out a little different.
    """

    def __init__(self, windows: list, voices: str, chars: int) -> None:
        self.windows = windows
        self.voices = voices
        self.chars = chars
        self.audio: Dict[int, bytes] = {}


_partials: "OrderedDict[str, Partial]" = OrderedDict()
MAX_PARTIALS = 4


class Job:
    def __init__(self, digest: str, total: int) -> None:
        self.digest = digest
        self.total = total
        self.done = 0
        self.error: Optional[str] = None
        self.failed_at = 0.0


_jobs: Dict[int, Job] = {}
_jobs_lock = threading.Lock()
# One story is being read by the speech service at a time: gentle on the free rate limit and
# on the small free server. Later ones wait their turn (the page shows "preparing").
_turn = threading.Semaphore(1)


def _audio_response(audio: bytes, digest: str, request: Request) -> Response:
    etag = f'"{digest[:32]}"'
    # private: the audio of a members-only story must never sit in a shared cache.
    headers = {"ETag": etag, "Cache-Control": "private, max-age=3600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=audio, media_type="audio/mpeg", headers=headers)


def _working(job: Job) -> Dict[str, object]:
    return {"state": "working", "done": job.done, "total": job.total}


def _run(post_id: int, digest: str, title: str, content: str) -> None:
    """Background job: cast the story, read it in the chosen voices, store the MP3, record usage."""
    job = _jobs[post_id]

    def progress(done: int, total: int) -> None:
        job.done = done

    try:
        with _turn:
            partial = _partials.get(digest)
            if partial is None:
                windows, _cast = plan_story(post_id, title, content)
                voices = ",".join(dict.fromkeys(v for w in windows for v in w.voices))[:60]
                partial = _partials[digest] = Partial(windows, voices, spoken_chars(windows))
                while len(_partials) > MAX_PARTIALS:
                    _partials.popitem(last=False)
            job.total = len(partial.windows)
            chars, voices = partial.chars, partial.voices
            audio = synthesize(partial.windows, STYLE, DIALOGUE_STYLE, progress, partial.audio)
        _partials.pop(digest, None)
        month = datetime.utcnow().strftime("%Y-%m")
        db = SessionLocal()
        try:
            row = db.query(StoryAudio).filter(StoryAudio.post_id == post_id).first()
            if row:
                row.content_hash, row.voice, row.audio, row.chars = digest, voices, audio, chars
                row.created_at = datetime.utcnow()
            else:
                db.add(StoryAudio(post_id=post_id, content_hash=digest, voice=voices, audio=audio, chars=chars))
            usage = db.query(TtsUsage).filter(TtsUsage.month == month).first()
            if usage:
                usage.chars += chars
            else:
                db.add(TtsUsage(month=month, chars=chars))
            db.commit()
        finally:
            db.close()
        with _jobs_lock:
            _jobs.pop(post_id, None)
    except SpeechUnavailable as exc:
        job.error = str(exc)
        job.failed_at = time.time()
    except Exception:  # never leave a listener waiting on a job that can no longer finish
        logger.exception("narration job crashed")
        job.error = "crashed"
        job.failed_at = time.time()


@router.get("/status")
def audio_status():
    """Whether the server can narrate right now, so the page picks its voice before the tap."""
    return {"enabled": is_available()}


@router.get("/post/{post_id}")
def get_story_audio(
    post_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if not is_available():
        raise HTTPException(status_code=503, detail="सर्वर की आवाज़ अभी उपलब्ध नहीं है")

    post = db.query(Post).filter(Post.id == post_id, Post.published == 1).first()
    if not post:
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    content = post.content or ""
    if not content.strip():
        raise HTTPException(status_code=404, detail="सुनाने के लिए कुछ नहीं है")
    # Same rule as reading: a signed-out visitor only gets the short, ungated stories.
    if current_user is None and len(content) > PREVIEW_LIMIT:
        raise HTTPException(status_code=401, detail="पूरी रचना सुनने के लिए साइन इन करें")
    if len(content) > settings.TTS_MAX_STORY_CHARS:
        raise HTTPException(status_code=413, detail="यह रचना सर्वर की आवाज़ के लिए बहुत लंबी है")

    digest = fingerprint(post.title or "", content)
    row = db.query(StoryAudio).filter(StoryAudio.post_id == post_id).first()
    if row and row.content_hash == digest:
        return _audio_response(row.audio, digest, request)

    with _jobs_lock:
        job = _jobs.get(post_id)
        if job and job.error:
            if time.time() - job.failed_at > FAILURE_MEMORY:
                _jobs.pop(post_id, None)
                job = None
            else:
                raise HTTPException(status_code=503, detail="सर्वर की आवाज़ अभी उपलब्ध नहीं है")
        if job:
            return JSONResponse(_working(job), status_code=202)

        # Nobody is making this story's audio yet: start it, if the allowances permit.
        key = f"tts:{current_user.id}" if current_user else f"tts-ip:{client_ip(request)}"
        enforce(actions, key, GENERATIONS_PER_HOUR, 3600, record=True)
        # Quotes are read as separate pieces, so the story's own length is close to what is sent.
        chars = len(content) + len(post.title or "")
        month = datetime.utcnow().strftime("%Y-%m")
        usage = db.query(TtsUsage).filter(TtsUsage.month == month).first()
        if (usage.chars if usage else 0) + chars > settings.TTS_MONTHLY_CHARS:
            raise HTTPException(status_code=503, detail="इस महीने की सर्वर-आवाज़ की सीमा पूरी हो चुकी है")
        job = Job(digest, 0)
        _jobs[post_id] = job
        threading.Thread(target=_run, args=(post_id, digest, post.title or "", content), daemon=True).start()
        return JSONResponse(_working(job), status_code=202)

