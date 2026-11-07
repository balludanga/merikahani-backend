"""Where narration audio is kept: in the database, in a folder, or in an object-storage service.

A narration is an MP3 of a few hundred kilobytes to a few megabytes, and the database it started in is
small and costs the most per megabyte. Keeping the bytes in a store of their own leaves the database
for what only a database can do. The row in ``story_audio`` stays either way: it says *where* the bytes
are (``storage`` and ``blob_key``), so a story can be moved without anything else knowing.

Three places, chosen by AUDIO_STORAGE:

  * ``db``     the bytes sit in the row, as they always have. The default, and what any failure falls back to.
  * ``local``  files in AUDIO_LOCAL_DIR. For development, or a host with a disk that survives a deploy.
  * ``remote`` an object-storage service. SKELETON: no service has been chosen, so ``RemoteStore`` only
               marks where one plugs in (see its docstring). Until then it counts as unavailable.

Narration must never break because of this. If the chosen place cannot be used, a new narration is
simply kept in the database; if a stored one cannot be read, the page falls back to the browser's voice,
exactly as it does when the speech service is down.
"""

import logging
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import event, func
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.models.story_audio import StoryAudio

logger = logging.getLogger(__name__)

DB = "db"
CONTENT_TYPE = "audio/mpeg"


class BlobMissing(Exception):
    """The store has nothing under that key."""


class NotConnected(Exception):
    """A store whose connection has not been written yet (the skeleton)."""


def audio_key(post_id: int, digest: str) -> str:
    """The key a story's narration is stored under. It includes the fingerprint of the text, so a rewritten
    story gets a new key and the old file can never be mistaken for the new narration."""
    return f"audio/{int(post_id)}/{digest[:32]}.mp3"


class BlobStore(ABC):
    name: str = ""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = CONTENT_TYPE) -> None:
        """Store the bytes under the key, replacing anything there. Must not leave half a file behind."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """The bytes under the key, or raise ``BlobMissing``."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the key. Removing what is not there is not an error."""


class LocalStore(BlobStore):
    """Files under a folder. Keys can never reach outside it, and a file appears whole or not at all."""

    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError(f"key {key!r} leaves the store")
        return path

    def put(self, key: str, data: bytes, content_type: str = CONTENT_TYPE) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename: a reader (or a crash) never sees a partly written file.
        fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".part-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise BlobMissing(key)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


class RemoteStore(BlobStore):
    """SKELETON: an object-storage service. Nothing here talks to one yet.

    The owner has not chosen a service. Any S3-compatible one fits (the common ones all speak that
    protocol), and so does a service with its own REST API; only these three methods change. To connect:

      1. Create a PRIVATE bucket (narrations belong to the site: nothing should be fetchable without us)
         and put its details in OBJECT_STORE_ENDPOINT, OBJECT_STORE_BUCKET, OBJECT_STORE_ACCESS_KEY,
         OBJECT_STORE_SECRET_KEY and, if the service wants one, OBJECT_STORE_REGION.
      2. Write ``put``, ``get`` and ``delete`` below (``get`` raises ``BlobMissing`` for a missing key).
         tests/test_blobstore.py runs the whole audio path against the folder store; point the same tests at
         this one with a throwaway bucket before relying on it.
      3. Make ``configured()`` check those settings, set AUDIO_STORAGE=remote and redeploy. New narrations then
         go to the store.
      4. Move the existing ones with ``scripts/move_audio.py --to remote`` (it checks every file by reading it
         back before it empties the database row; run it with --dry-run first).

    Audio is served through the backend (the page's player fetches it with the reader's sign-in), so the
    bucket stays private. Handing out short-lived signed links instead is a later optimisation.
    """

    name = "remote"

    def configured(self) -> bool:
        return False  # skeleton: becomes "the OBJECT_STORE_* settings are present" once the methods exist

    def put(self, key: str, data: bytes, content_type: str = CONTENT_TYPE) -> None:
        raise NotConnected("remote object store: put is not written yet")

    def get(self, key: str) -> bytes:
        raise NotConnected("remote object store: get is not written yet")

    def delete(self, key: str) -> None:
        raise NotConnected("remote object store: delete is not written yet")


def store_named(name: str) -> Optional[BlobStore]:
    """The store with that name if it can be used right now, else None."""
    if name == "local":
        # A folder on a host that wipes its disk at every deploy would silently lose the audio: only
        # trust it in development, or when the owner has said the disk is permanent.
        if settings.debug or os.getenv("AUDIO_LOCAL_IS_PERMANENT", "").lower() in ("1", "true"):
            return LocalStore(settings.AUDIO_LOCAL_DIR)
        logger.warning("AUDIO_STORAGE=local ignored: set AUDIO_LOCAL_IS_PERMANENT=true if the disk survives deploys")
        return None
    if name == "remote":
        store = RemoteStore()
        return store if store.configured() else None
    return None


def chosen_store() -> Optional[BlobStore]:
    """Where new narrations should go: None means the database (the default, or the choice is unusable)."""
    wanted = settings.AUDIO_STORAGE
    if wanted in ("", DB):
        return None
    store = store_named(wanted)
    if store is None:
        logger.warning("AUDIO_STORAGE=%s is not usable; narration stays in the database", wanted)
    return store


# --------------------------------------------------------------------------- reading and writing a story's audio

def read_audio(row: StoryAudio) -> bytes:
    """The MP3 of a stored narration, wherever it is. Raises ``BlobMissing`` / ``NotConnected`` if it cannot be had."""
    if row.storage == DB:
        if row.audio is None:
            raise BlobMissing(f"story {row.post_id} has no audio in the database")
        return bytes(row.audio)
    store = store_named(row.storage)
    if store is None:
        raise NotConnected(f"{row.storage} store is not available")
    return store.get(row.blob_key)


def audio_size(row: StoryAudio) -> int:
    return int(row.size_bytes or len(row.audio or b""))


def save_audio(row: StoryAudio, data: bytes) -> Optional[Tuple[str, str]]:
    """Put a narration's bytes where they belong and set the row to match. Returns the (store, key) the row
    pointed at before, if that file is now unneeded (the caller deletes it once the row is committed)."""
    previous = (row.storage, row.blob_key) if row.storage != DB and row.blob_key else None
    store = chosen_store()
    stored_in, key = None, None
    if store is not None:
        key = audio_key(row.post_id, row.content_hash)
        try:
            store.put(key, data, CONTENT_TYPE)
            stored_in = store.name
        except Exception:  # noqa: BLE001 - narration is worth more than where it is filed
            logger.exception("could not store narration %s in %s; keeping it in the database", row.post_id, store.name)
    row.size_bytes = len(data)
    if stored_in:
        row.storage, row.blob_key, row.audio = stored_in, key, None
    else:
        row.storage, row.blob_key, row.audio = DB, None, data
    if previous and previous == (row.storage, row.blob_key):
        return None
    return previous


def forget(blob: Optional[Tuple[str, str]]) -> None:
    """Delete a file that no row points at any more. Never raises: a leftover file costs a little space, a
    failure here must not undo the narration that was just made."""
    if not blob:
        return
    name, key = blob
    store = store_named(name)
    if store is None:
        return
    try:
        store.delete(key)
    except Exception:  # noqa: BLE001
        logger.exception("could not delete %s from the %s store", key, name)


# A deleted story (or account) takes its narration with it. The row goes with the story through the
# database's cascade; the file does not, so the file is deleted once the deletion is really committed.
_PENDING = "blobs_to_forget"


@event.listens_for(StoryAudio, "after_delete")
def _queue_file_for_deletion(mapper, connection, target) -> None:
    session = object_session(target)
    if session is not None and target.storage != DB and target.blob_key:
        session.info.setdefault(_PENDING, []).append((target.storage, target.blob_key))


@event.listens_for(Session, "after_commit")
def _delete_queued_files(session) -> None:
    for blob in session.info.pop(_PENDING, []):
        forget(blob)


@event.listens_for(Session, "after_rollback")
def _drop_queued_files(session) -> None:
    session.info.pop(_PENDING, None)


# --------------------------------------------------------------------------- moving what exists

@dataclass
class MoveReport:
    moved: int = 0
    would_move: int = 0
    bytes: int = 0
    failed: List[Tuple[int, str]] = field(default_factory=list)
    skipped: int = 0


def move_to_store(db: Session, store: BlobStore, *, limit: Optional[int] = None, dry_run: bool = False) -> MoveReport:
    """Move narrations that are still in the database into ``store``.

    Each one is written, then READ BACK and compared byte for byte, and only then is its row pointed at the
    store and emptied. A failure on one leaves that row exactly as it was and moves on to the next."""
    report = MoveReport()
    query = db.query(StoryAudio).filter(StoryAudio.storage == DB, StoryAudio.audio.isnot(None)).order_by(StoryAudio.post_id)
    for row in query.limit(limit) if limit else query:
        data = bytes(row.audio)
        if dry_run:
            report.would_move += 1
            report.bytes += len(data)
            continue
        key = audio_key(row.post_id, row.content_hash)
        try:
            store.put(key, data, CONTENT_TYPE)
            if store.get(key) != data:
                raise IOError("what was read back differs from what was written")
        except Exception as exc:  # noqa: BLE001
            logger.exception("could not move narration %s", row.post_id)
            report.failed.append((row.post_id, str(exc)))
            continue
        row.storage, row.blob_key, row.size_bytes, row.audio = store.name, key, len(data), None
        db.commit()
        report.moved += 1
        report.bytes += len(data)
    return report


def move_back_to_db(db: Session, *, limit: Optional[int] = None, dry_run: bool = False) -> MoveReport:
    """The way back: pull narrations out of whatever store holds them and into the database again."""
    report = MoveReport()
    query = db.query(StoryAudio).filter(StoryAudio.storage != DB).order_by(StoryAudio.post_id)
    for row in query.limit(limit) if limit else query:
        if dry_run:
            report.would_move += 1
            report.bytes += int(row.size_bytes or 0)
            continue
        try:
            data = read_audio(row)
        except Exception as exc:  # noqa: BLE001
            logger.exception("could not read narration %s back", row.post_id)
            report.failed.append((row.post_id, str(exc)))
            continue
        blob = (row.storage, row.blob_key)
        row.storage, row.blob_key, row.size_bytes, row.audio = DB, None, len(data), data
        db.commit()
        forget(blob)
        report.moved += 1
        report.bytes += len(data)
    return report


def counts(db: Session) -> Dict[str, int]:
    """How many narrations are kept in each place (for the owner's status page and the move script)."""
    return {name: int(n) for name, n in db.query(StoryAudio.storage, func.count()).group_by(StoryAudio.storage).all()}
