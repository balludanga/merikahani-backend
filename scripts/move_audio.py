"""Move narration audio between the database and an object store (see app/core/blobstore.py).

    # what would move, without touching anything
    .venv/bin/python scripts/move_audio.py --to local --dry-run

    # move everything still in the database into the store named by --to (local | remote)
    .venv/bin/python scripts/move_audio.py --to remote

    # the way back: pull everything out of the store and into the database again
    .venv/bin/python scripts/move_audio.py --back

Run it with the same DATABASE_URL (and OBJECT_STORE_* settings) as the site. Every file is written, READ BACK
and compared before its database row is emptied, so a store that loses or garbles a file leaves that story's
audio where it was. It is safe to stop and run again: what already moved stays moved.

The database keeps the space of the audio it no longer holds until it is vacuumed. On Postgres (Supabase:
SQL editor) run   VACUUM FULL story_audio;   once afterwards to give the space back (it briefly locks the table).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import blobstore  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def show(db, title):
    places = blobstore.counts(db)
    print(f"{title}: " + (", ".join(f"{name} {n}" for name, n in sorted(places.items())) or "no narrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--to", choices=["local", "remote"], help="the store to move narrations into")
    where.add_argument("--back", action="store_true", help="move narrations from their store back into the database")
    parser.add_argument("--limit", type=int, help="move at most this many (to try a few first)")
    parser.add_argument("--dry-run", action="store_true", help="only say what would move")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        show(db, "before")
        if args.back:
            report = blobstore.move_back_to_db(db, limit=args.limit, dry_run=args.dry_run)
        else:
            store = blobstore.store_named(args.to)
            if store is None:
                print(f"The {args.to} store is not usable here (not connected, or a local folder outside development).")
                return 2
            report = blobstore.move_to_store(db, store, limit=args.limit, dry_run=args.dry_run)
        megabytes = report.bytes / (1024 * 1024)
        if args.dry_run:
            print(f"would move {report.would_move} narrations, {megabytes:.1f} MB")
        else:
            print(f"moved {report.moved} narrations, {megabytes:.1f} MB")
            show(db, "after")
        for post_id, reason in report.failed:
            print(f"  left as it was: story {post_id}: {reason}")
        if report.moved and not args.dry_run and not args.back:
            print("Now run  VACUUM FULL story_audio;  on Postgres to give the space back.")
        return 1 if report.failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
