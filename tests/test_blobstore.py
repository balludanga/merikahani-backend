"""Narration audio outside the database: the folder store, the audio endpoints on top of it, and the mover."""

import os
import uuid
from unittest.mock import patch

import pytest

from app.api.endpoints import audio as audio_endpoint
from app.core import blobstore, narration
from app.core.blobstore import BlobMissing, LocalStore, NotConnected, RemoteStore, audio_key
from app.core.config import settings
from app.database import SessionLocal
from app.models.story_audio import StoryAudio

# The speech fakes and the "ask until it is ready" helper are the audio suite's own.
from tests.test_audio import SHORT, _clean, _listen, _post, gemini_on  # noqa: F401 - fixtures are used by name


@pytest.fixture(autouse=True)
def _no_narrations_pointing_at_folders_that_are_gone():
    """The test database outlives each test's temporary folder: forget rows whose files lived in one."""
    def forget_them():
        with SessionLocal() as db:
            db.query(StoryAudio).filter(StoryAudio.storage != "db").delete(synchronize_session=False)
            db.commit()

    forget_them()
    yield
    forget_them()


@pytest.fixture()
def folder(tmp_path):
    """New narrations go to a throwaway folder."""
    with patch.object(settings, "AUDIO_STORAGE", "local"), patch.object(settings, "AUDIO_LOCAL_DIR", str(tmp_path)):
        yield tmp_path


def the_row(post_id):
    with SessionLocal() as db:
        row = db.query(StoryAudio).filter(StoryAudio.post_id == post_id).one()
        db.expunge(row)
        return row


def database_row(post_id, data, digest=None):
    """A narration of the old kind: its bytes in the database."""
    with SessionLocal() as db:
        db.add(StoryAudio(post_id=post_id, content_hash=digest or uuid.uuid4().hex * 2, voice="v", audio=data, chars=10, size_bytes=len(data)))
        db.commit()


def files_in(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


# --------------------------------------------------------------------------- the folder store

def test_a_folder_store_keeps_replaces_and_forgets(tmp_path):
    store = LocalStore(str(tmp_path))
    store.put("audio/1/a.mp3", b"first")
    assert store.get("audio/1/a.mp3") == b"first"
    store.put("audio/1/a.mp3", b"second")
    assert store.get("audio/1/a.mp3") == b"second" and files_in(tmp_path) == ["audio/1/a.mp3"]
    store.delete("audio/1/a.mp3")
    store.delete("audio/1/a.mp3")  # forgetting what is not there is fine
    with pytest.raises(BlobMissing):
        store.get("audio/1/a.mp3")


def test_a_key_can_never_reach_outside_the_folder(tmp_path):
    store = LocalStore(str(tmp_path / "inside"))
    for bad in ("../outside.mp3", "audio/../../outside.mp3", "/etc/passwd"):
        with pytest.raises(ValueError):
            store.put(bad, b"x")
    assert not (tmp_path / "outside.mp3").exists()


def test_a_write_that_fails_leaves_no_half_file_and_keeps_the_old_one(tmp_path):
    store = LocalStore(str(tmp_path))
    store.put("audio/2/b.mp3", b"good")
    with patch("app.core.blobstore.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.put("audio/2/b.mp3", b"new but never lands")
    assert store.get("audio/2/b.mp3") == b"good" and files_in(tmp_path) == ["audio/2/b.mp3"]  # no ".part-" litter


def test_the_key_names_the_story_and_its_text():
    assert audio_key(7, "abcdef" * 11) == "audio/7/abcdefabcdefabcdefabcdefabcdefab.mp3"
    assert audio_key(7, "a" * 64) != audio_key(7, "b" * 64) != audio_key(8, "b" * 64)


# --------------------------------------------------------------------------- which place is used

def test_the_database_is_the_default_and_an_unusable_choice_falls_back_to_it(tmp_path):
    assert blobstore.chosen_store() is None
    with patch.object(settings, "AUDIO_STORAGE", "local"), patch.object(settings, "AUDIO_LOCAL_DIR", str(tmp_path)):
        assert isinstance(blobstore.chosen_store(), LocalStore)
        # A folder on a host that wipes its disk each deploy would quietly lose audio: refused outside development.
        with patch.object(settings, "debug", False):
            assert blobstore.chosen_store() is None
            with patch.dict(os.environ, {"AUDIO_LOCAL_IS_PERMANENT": "true"}):
                assert isinstance(blobstore.chosen_store(), LocalStore)
    for choice in ("remote", "somewhere-else"):  # the skeleton, and a name nobody knows
        with patch.object(settings, "AUDIO_STORAGE", choice):
            assert blobstore.chosen_store() is None


def test_the_remote_store_is_a_skeleton_that_refuses_to_pretend():
    remote = RemoteStore()
    assert remote.configured() is False
    for call in (lambda: remote.put("k", b"x"), lambda: remote.get("k"), lambda: remote.delete("k")):
        with pytest.raises(NotConnected):
            call()


# --------------------------------------------------------------------------- the audio endpoints on top of it

def test_a_new_narration_goes_to_the_store_and_plays_from_it(test_client, writer_a, gemini_on, folder):
    post = _post(test_client, writer_a, SHORT + " फ़ोल्डर में।", title="फ़ोल्डर")
    first = _listen(test_client, post["id"])
    assert first.status_code == 200 and first.content.startswith(b"ID3-fake-mp3-")

    row = the_row(post["id"])
    assert row.storage == "local" and row.audio is None and row.size_bytes == len(first.content)
    assert row.blob_key == audio_key(post["id"], row.content_hash) and files_in(folder) == [row.blob_key]
    again = test_client.get(f"/api/audio/post/{post['id']}")
    assert again.status_code == 200 and again.content == first.content and again.headers["content-type"] == "audio/mpeg"
    assert test_client.get(f"/api/audio/status?post_id={post['id']}").json()["ready"] is True


def test_the_page_asking_again_is_answered_without_reading_the_file(test_client, writer_a, gemini_on, folder):
    post = _post(test_client, writer_a, SHORT + " फिर वही।", title="बिना पढ़े")
    etag = _listen(test_client, post["id"]).headers["etag"]
    os.unlink(folder / the_row(post["id"]).blob_key)  # the file is gone ...
    assert test_client.get(f"/api/audio/post/{post['id']}", headers={"If-None-Match": etag}).status_code == 304  # ... "unchanged" needs none
    assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 503  # but a real fetch cannot be answered


def test_a_store_that_cannot_be_written_to_does_not_lose_the_narration(test_client, writer_a, gemini_on, folder):
    post = _post(test_client, writer_a, SHORT + " डिस्क भरी।", title="डिस्क भरी")
    with patch.object(LocalStore, "put", side_effect=OSError("disk full")):
        response = _listen(test_client, post["id"])
    assert response.status_code == 200
    row = the_row(post["id"])
    assert row.storage == "db" and row.audio == response.content and row.blob_key is None and files_in(folder) == []


def test_a_narration_recorded_in_a_store_that_is_not_there_falls_back_to_the_browser_voice(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, SHORT + " कहीं और।", title="कहीं और")
    digest = narration.fingerprint("कहीं और", SHORT + " कहीं और।")
    with SessionLocal() as db:
        db.add(StoryAudio(post_id=post["id"], content_hash=digest, voice="v", audio=None, storage="remote", blob_key="audio/x.mp3", size_bytes=10, chars=3))
        db.commit()
    assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 503


def test_the_word_times_estimate_works_for_audio_that_lives_in_a_store(test_client, writer_a, folder):
    content = "पहली पंक्ति यहाँ है।\nदूसरी पंक्ति भी यहाँ है।"
    post = _post(test_client, writer_a, content, title="स्टोर में पुरानी")
    digest = narration.fingerprint("स्टोर में पुरानी", content)
    key = audio_key(post["id"], digest)
    LocalStore(str(folder)).put(key, b"x" * 12000)
    with SessionLocal() as db:
        db.add(StoryAudio(post_id=post["id"], content_hash=digest, voice="v", audio=None, storage="local", blob_key=key, size_bytes=12000, chars=40))
        db.commit()
    assert test_client.get(f"/api/audio/timings/{post['id']}").json()["d"] == 2.0  # 12000 bytes at 48 kbps


def test_a_rewritten_story_gets_a_new_file_and_the_old_one_is_removed(test_client, writer_a, gemini_on, folder):
    post = _post(test_client, writer_a, SHORT + " पहला रूप।", title="बदलती")
    _listen(test_client, post["id"])
    first_key = the_row(post["id"]).blob_key
    test_client.put(f"/api/posts/{post['id']}", json={"content": SHORT + " दूसरा रूप।"}, headers=writer_a["headers"])
    _listen(test_client, post["id"])
    second_key = the_row(post["id"]).blob_key
    assert second_key != first_key and files_in(folder) == [second_key]


def test_deleting_a_story_deletes_its_narration_file_too(test_client, writer_a, gemini_on, folder):
    post = _post(test_client, writer_a, SHORT + " मिटने वाली।", title="मिटेगी")
    _listen(test_client, post["id"])
    assert len(files_in(folder)) == 1
    assert test_client.delete(f"/api/posts/{post['id']}", headers=writer_a["headers"]).status_code in (200, 204)
    assert files_in(folder) == []


def test_a_deletion_that_is_rolled_back_keeps_the_file(test_client, writer_a, gemini_on, folder):
    post = _post(test_client, writer_a, SHORT + " बचने वाली।", title="बचेगी")
    _listen(test_client, post["id"])
    with SessionLocal() as db:
        db.delete(db.query(StoryAudio).filter(StoryAudio.post_id == post["id"]).one())
        db.flush()
        db.rollback()
    assert len(files_in(folder)) == 1


# --------------------------------------------------------------------------- moving what already exists

def three_old_narrations(test_client, writer_a):
    ids = [_post(test_client, writer_a, SHORT + f" पुरानी {n}।", title=f"पुरानी {n} {uuid.uuid4().hex[:4]}")["id"] for n in range(3)]
    for number, post_id in enumerate(ids):
        database_row(post_id, f"old-audio-{number}".encode() * 50)
    return ids


def test_moving_checks_each_file_before_it_empties_the_row(test_client, writer_a, folder):
    ids = three_old_narrations(test_client, writer_a)
    store = LocalStore(str(folder))
    originals = {i: bytes(the_row(i).audio) for i in ids}

    with SessionLocal() as db:
        preview = blobstore.move_to_store(db, store, dry_run=True)
    assert preview.would_move >= 3 and preview.moved == 0 and files_in(folder) == []
    assert all(the_row(i).storage == "db" for i in ids)  # a dry run touches nothing

    with SessionLocal() as db:
        report = blobstore.move_to_store(db, store)
    assert report.failed == [] and report.moved >= 3
    for post_id in ids:
        row = the_row(post_id)
        assert (row.storage, row.audio) == ("local", None) and row.size_bytes == len(originals[post_id])
        assert store.get(row.blob_key) == originals[post_id]
    with SessionLocal() as db:
        assert blobstore.move_to_store(db, store).moved == 0  # nothing left to move: safe to run again


def test_a_file_that_cannot_be_stored_or_read_back_intact_stays_in_the_database(test_client, writer_a, folder):
    ids = three_old_narrations(test_client, writer_a)
    store = LocalStore(str(folder))
    broken_put, garbled = ids[0], ids[1]

    real_put, real_get = store.put, store.get
    store.put = lambda key, data, content_type="audio/mpeg": (_ for _ in ()).throw(OSError("no space")) if f"/{broken_put}/" in key else real_put(key, data, content_type)
    store.get = lambda key: b"garbage" if f"/{garbled}/" in key else real_get(key)

    with SessionLocal() as db:
        report = blobstore.move_to_store(db, store)
    failed = {post_id for post_id, _ in report.failed}
    assert failed >= {broken_put, garbled}
    for post_id in (broken_put, garbled):
        assert the_row(post_id).storage == "db" and the_row(post_id).audio  # exactly as it was
    assert the_row(ids[2]).storage == "local" and the_row(ids[2]).audio is None


def test_everything_can_be_moved_back_into_the_database(test_client, writer_a, folder):
    ids = three_old_narrations(test_client, writer_a)
    originals = {i: bytes(the_row(i).audio) for i in ids}
    store = LocalStore(str(folder))
    with SessionLocal() as db:
        blobstore.move_to_store(db, store)
    assert len(files_in(folder)) >= 3

    with SessionLocal() as db:
        report = blobstore.move_back_to_db(db)
    assert report.failed == []
    for post_id in ids:
        row = the_row(post_id)
        assert (row.storage, row.blob_key, bytes(row.audio)) == ("db", None, originals[post_id])
    assert files_in(folder) == []
    with SessionLocal() as db:
        assert blobstore.counts(db).get("local", 0) == 0


def test_the_move_script_refuses_a_store_that_is_not_connected(capsys):
    import runpy
    import sys

    with patch.object(sys, "argv", ["move_audio.py", "--to", "remote", "--dry-run"]):
        with pytest.raises(SystemExit) as stopped:
            runpy.run_path("scripts/move_audio.py", run_name="__main__")
    assert stopped.value.code == 2 and "not usable" in capsys.readouterr().out
