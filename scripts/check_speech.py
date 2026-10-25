"""Hear the narration before relying on it, and verify the Gemini setup.

    # list Hindi / India voices in Google's library
    .venv/bin/python scripts/check_speech.py

    # one sample passage in each named voice, to compare by ear
    .venv/bin/python scripts/check_speech.py --voices hi-in-storyteller-1,Sulafat --out ~/Desktop/voice_samples

    # a whole story the way the site reads it: who narrates, who speaks, which voices
    .venv/bin/python scripts/check_speech.py --story story.txt --title "शीर्षक" --post-id 7 --out ~/Desktop/voice_samples

Reads GEMINI_API_KEY from the environment or backend/.env (never printed).
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import speech  # noqa: E402
from app.core.casting import plan_story  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.narration import DIALOGUE_STYLE, STYLE, Turn, Window  # noqa: E402

SAMPLE_TITLE = "बारिश की वह शाम"
SAMPLE = (
    "बारिश की वह शाम मुझे आज भी याद है। माँ चूल्हे के पास बैठी थीं, और मैं खिड़की से बाहर देख रहा था।\n"
    "“क्या तुम अब भी नाराज़ हो?” उन्होंने धीरे से पूछा।\n"
    "मैंने कुछ नहीं कहा… बस चुपचाप चाय का कप थामे रहा।"
)


def list_voices() -> None:
    headers = {"x-goog-api-key": settings.GEMINI_API_KEY}
    for label, params in (("language hi-IN", {"language_code": "hi-IN"}), ("region IN", {"region_code": "IN"})):
        response = requests.get(speech.VOICES_URL, headers=headers, params={**params, "page_size": 100}, timeout=30)
        print(f"\n== library voices for {label}: HTTP {response.status_code}")
        if response.status_code != 200:
            print(response.text[:300])
            continue
        for voice in response.json().get("voices", []):
            print(f"  {voice.get('id')}  {voice.get('display_name')}  accent={voice.get('accent')}  "
                  f"{voice.get('gender')}  {voice.get('description', '')[:70]}")


def save(mp3: bytes, out: str, name: str) -> None:
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"{name}.mp3")
    with open(path, "wb") as f:
        f.write(mp3)
    print(f"  saved {path} ({len(mp3) // 1024} KB)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voices", default="", help="comma-separated voice names / ids to record")
    parser.add_argument("--story", default="", help="a text file: read it as the site would")
    parser.add_argument("--title", default="")
    parser.add_argument("--post-id", type=int, default=1, help="the voice draw is seeded by this")
    parser.add_argument("--out", default="voice_samples")
    args = parser.parse_args()

    if not settings.GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set (put it in backend/.env or the environment).")
        return 1
    out = os.path.expanduser(args.out)

    if args.story:
        with open(os.path.expanduser(args.story), encoding="utf-8") as f:
            text = f.read()
        windows, cast = plan_story(args.post_id, args.title, text)
        print(f"narrator: {cast.narrator_gender}; speakers: "
              f"{ {s.id: (s.name, s.gender) for s in cast.speakers.values()} }")
        for number, w in enumerate(windows, start=1):
            print(f"  request {number}: voices {w.voices}, +{w.pause}s before")
            for t in w.turns:
                print(f"     [{t.voice:22}] {'dialogue ' if t.dialogue else 'narration'} {t.text[:56]!r}")
        try:
            mp3 = speech.synthesize(windows, STYLE, DIALOGUE_STYLE, lambda d, t: print(f"  done {d}/{t}", flush=True))
        except speech.SpeechUnavailable as exc:
            print(f"failed: {exc} (see the log line above for what Google answered)")
            return 1
        save(mp3, out, f"story-{args.post_id}")
        return 0

    names = [v.strip() for v in args.voices.split(",") if v.strip()]
    if not names:
        list_voices()
        print("\nPass --voices NAME1,NAME2 to record a sample of each, or --story FILE for a whole story.")
        return 0
    for name in names:
        print(f"\nrecording {name} ...", flush=True)
        try:
            mp3 = speech.synthesize([Window([Turn(name, False, f"{SAMPLE_TITLE}\n\n{SAMPLE}", True)])], STYLE, DIALOGUE_STYLE)
        except speech.SpeechUnavailable as exc:
            print(f"  failed: {exc}")
            continue
        save(mp3, out, name)
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=os.environ.get("LOGLEVEL", "WARNING"))
    sys.exit(main())
