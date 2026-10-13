"""
Add the October cast's library voices to the ElevenLabs account.

The elevenlabs-v4 voice_map (configs/models.yaml) names eight Indian-accented
voices from the ElevenLabs library. The API only speaks with voices that are
in the account, and each one takes a voice slot, so they are added once,
before the first run - not by the runner, which never changes an account.

    python tools/add_elevenlabs_voices.py            # what is missing, and the slots left
    python tools/add_elevenlabs_voices.py --apply    # add the missing ones

Reads ELEVENLABS_API_KEY from the environment (voice/.env). Adding a voice
costs no characters; it uses a voice slot.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io"

# voice_id -> (public_owner_id, name to save it under). The owner id comes
# from the library listing (/v1/shared-voices) and is needed to add a voice.
CAST = {
    "ecp3DWciuUyW7BYM7II1": ("7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6", "oct-female_young-Anika"),
    "vYENaCJHl4vFKNDYPr8y": ("f87f057c2250691953ac2e6227859706764bd08c88a055a18c74261957885a51", "oct-female_mid-Riya Rao"),
    "OUBnvvuqEKdDWtapoJFn": ("7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6", "oct-female_older-Tia Mirza"),
    "wSMP2ehaozb5I36yi8vg": ("7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6", "oct-female_elder-Dulari Didi"),
    "nwXBqbMKJWkQdYCbhxqZ": ("7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6", "oct-male_teen-Benne"),
    "1U02n4nD6AdIZ9CjF053": ("f87f057c2250691953ac2e6227859706764bd08c88a055a18c74261957885a51", "oct-male_young-Viraj"),
    "EitqXD7jgIy0K5Z1zGGp": ("7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6", "oct-male_mid-Rudra"),
    "H53NeKIHK4upDFLnIv9Z": ("7398804d9eaf2f463899a907587c33a390591775784f87857b6d0e1e4e3e66f6", "oct-male_older-Aakash Aryan"),
}


def call(key: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="add the missing voices (uses voice slots)")
    args = ap.parse_args()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print("ELEVENLABS_API_KEY is not set (source voice/.env first)", file=sys.stderr)
        return 2

    sub = call(key, "/v1/user/subscription")
    have = {v["voice_id"] for v in call(key, "/v2/voices?page_size=100")["voices"]}
    # A library voice added to the account keeps its voice_id, but some
    # accounts get a new id; match on the saved name too.
    names = {v["name"] for v in call(key, "/v2/voices?page_size=100")["voices"]}
    missing = [vid for vid, (_, name) in CAST.items() if vid not in have and name not in names]
    free = sub["voice_limit"] - sub.get("voice_slots_used", 0)
    print(f"plan {sub['tier']}: {sub.get('voice_slots_used', 0)} of {sub['voice_limit']} voice slots used, {free} free")
    print(f"{len(CAST) - len(missing)} of {len(CAST)} cast voices already in the account")
    for vid in missing:
        print(f"  missing {vid}  {CAST[vid][1]}")
    if not missing or not args.apply:
        return 0
    if len(missing) > free:
        print(f"not enough voice slots: need {len(missing)}, have {free}. Move to a plan with more slots.",
              file=sys.stderr)
        return 1
    for vid in missing:
        owner, name = CAST[vid]
        res = call(key, f"/v1/voices/add/{owner}/{vid}", {"new_name": name})
        got = res.get("voice_id")
        print(f"  added   {vid} as {name}" + (f" (new id {got} - update models.yaml)" if got and got != vid else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
