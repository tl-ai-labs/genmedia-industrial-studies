"""Declare the frozen assets under `inputs:` in the pending scenario YAMLs.

Only assets that exist on disk WITH a sidecar are wired; anything missing is
reported loudly and the scenario keeps no `inputs:` key (so a run containing
it is never rejected for a phantom file). VID-AVA-04 also gets its Hindi
script under `input.script`. Re-runnable.

    python assets/wire_inputs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

VIDEO = Path(__file__).resolve().parent.parent
BANK = VIDEO / "assets" / "bank"
PENDING = VIDEO / "scenarios" / "bank-video-pending"

# scenario -> {role: asset file}; paths are relative to the video/ project root
WIRING = {
    "VID-AD-01": {"reference": "VID-AD-01-reference.png"},
    "VID-AD-02": {"reference": "VID-AD-02-reference.png"},
    "VID-AD-03": {"reference": "VID-AD-03-reference.png"},
    "VID-AD-04": {"reference": "VID-AD-04-reference.png"},
    "VID-AD-05": {"logo": "VID-AD-05-logo.png"},
    "VID-AD-06": {"reference": "VID-AD-06-reference.png"},
    "VID-AD-07": {"reference": "VID-AD-07-reference.png"},
    "VID-AD-08": {"reference1": "VID-AD-08-reference1.png",
                  "reference2": "VID-AD-08-reference2.png",
                  "reference3": "VID-AD-08-reference3.png"},
    "VID-AD-10": {"reference": "VID-AD-10-reference.png", "logo": "VID-AD-10-logo.png"},
    "VID-I2V-01": {"photo": "VID-I2V-01-photo.png"},
    "VID-I2V-02": {"first_frame": "VID-I2V-02-first_frame.png"},
    "VID-I2V-03": {"first_frame": "VID-I2V-03-first_frame.png",
                   "last_frame": "VID-I2V-03-last_frame.png"},
    "VID-I2V-04": {"character": "VID-I2V-04-character.png"},
    "VID-I2V-05": {"reference": "VID-I2V-05-reference.png"},
    "VID-I2V-06": {"landscape": "VID-I2V-06-landscape.png"},
    "VID-I2V-07": {"character": "VID-I2V-07-character.png",
                   "environment": "VID-I2V-07-environment.png"},
    "VID-I2V-08": {"style": "VID-I2V-08-style.png"},
    "VID-I2V-09": {"logo": "VID-I2V-09-logo.png"},
    "VID-I2V-10": {"still1": "VID-I2V-10-still1.png", "still2": "VID-I2V-10-still2.png",
                   "still3": "VID-I2V-10-still3.png"},
    "VID-AVA-01": {"presenter": "VID-AVA-01-presenter.png"},
    "VID-AVA-02": {"presenter1": "VID-AVA-02-presenter1.png",
                   "presenter2": "VID-AVA-02-presenter2.png"},
    "VID-AVA-04": {"presenter": "VID-AVA-04-presenter.png"},
    "VID-AVA-06": {"product": "VID-AVA-06-product.png"},
    **{f"VID-EDIT-{n:02d}": {"source": f"VID-EDIT-{n:02d}-source.mp4"}
       for n in (1, 2, 3, 4, 5, 6, 7, 8, 9)},
}
# VID-EDIT-10 edits the VID-EDIT-08 output — derived at run time, never wired.


def main() -> int:
    wired = missing = 0
    for sid, roles in WIRING.items():
        path = PENDING / f"{sid}.yaml"
        text = path.read_text()
        head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
        doc = yaml.safe_load(text)
        inputs = {}
        for role, fname in roles.items():
            f = BANK / fname
            if f.exists() and f.with_suffix(".json").exists():
                inputs[role] = f"assets/bank/{fname}"
            else:
                print(f"  MISSING {sid} {role}: assets/bank/{fname}"
                      f"{'' if f.exists() else ' (file)'}"
                      f"{'' if f.with_suffix('.json').exists() else ' (sidecar)'}")
                missing += 1
        if inputs:
            doc["inputs"] = inputs
        else:
            doc.pop("inputs", None)
        if sid == "VID-AVA-04":
            script = BANK / "VID-AVA-04-script.hi.txt"
            if script.exists():
                doc["input"] = {"script": script.read_text(encoding="utf-8").strip(),
                                "language": "hi"}
        # keep key order stable: id, modality, task, title, prompt, [input], [inputs], params, ...
        order = ["id", "modality", "task", "title", "prompt", "input", "inputs", "params",
                 "expected", "checks", "tags"]
        doc = {k: doc[k] for k in order if k in doc}
        path.write_text(head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=88))
        wired += len(inputs)
    print(f"[wire] {wired} inputs wired across {len(WIRING)} scenarios; {missing} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
