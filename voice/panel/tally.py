#!/usr/bin/env python3
"""
Tally the blind panel votes.

Reads reveal.json (the de-blind key) and every exports/*.json a reviewer sent
back, turns each A/B choice into a model, and reports:

  - preference per model, pooled over all reviewers
  - a two-sided sign test over the decided (non-tie) votes
  - the per-repo verdict rule: a side needs >= 70% of decided votes AND
    n >= 10 decided to be called a panel preference
  - per-card leaning
  - inter-reviewer agreement, if two or more people voted
  - the identical-pair integrity checks: who did not answer "no preference"

    python voice/panel/tally.py
    python voice/panel/tally.py exports/*.json

Writes tally.md beside this script. Offline, no dependencies.
"""

from __future__ import annotations

import glob
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

PANEL = Path(__file__).resolve().parent
WIN_FRAC = 0.70
MIN_DECIDED = 10


def sign_test_p(k: int, n: int) -> float:
    """Two-sided probability of >= k successes in n fair coin flips."""
    if n == 0:
        return 1.0
    k = max(k, n - k)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> None:
    reveal_path = PANEL / "reveal.json"
    if not reveal_path.exists():
        sys.exit("reveal.json not found -- run build.py first")
    reveal = json.loads(reveal_path.read_text())

    args = sys.argv[1:]
    files = [Path(p) for p in args] if args else sorted((PANEL / "exports").glob("*.json"))
    files = [f for f in files if f.name != ".gitkeep"]
    if not files:
        sys.exit(f"no export files. put reviewer JSON in {PANEL / 'exports'}/ and re-run")

    wins: dict[str, int] = defaultdict(int)
    ties = 0
    decided = 0
    per_card: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_reviewer_card: dict[str, dict[str, str]] = {}     # reviewer -> card -> model|"tie"
    integrity: dict[str, tuple[int, int]] = {}            # reviewer -> (passed, total)
    manifest_hashes: set[str] = set()
    skipped: list[str] = []

    for f in files:
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            skipped.append(f"{f.name}: not valid JSON ({e})")
            continue
        who = str(d.get("reviewer") or f.stem)
        manifest_hashes.add(d.get("manifestHash", "?"))
        seen: dict[str, str] = {}
        ipass = itot = 0

        for v in d.get("votes", []):
            choice = v.get("choice")
            left, right = v.get("left"), v.get("right")
            if v.get("attn") or str(v.get("card", "")).startswith("attn-"):
                itot += 1
                if choice == "tie":
                    ipass += 1
                continue
            if choice not in ("left", "right", "tie"):
                continue
            lm = (reveal.get(left) or {}).get("model")
            rm = (reveal.get(right) or {}).get("model")
            if not lm or not rm:
                skipped.append(f"{who}/{v.get('card')}: clip not in reveal.json")
                continue
            cardid = v.get("card")
            if choice == "tie":
                ties += 1
                per_card[cardid]["tie"] += 1
                seen[cardid] = "tie"
            else:
                winner = lm if choice == "left" else rm
                wins[winner] += 1
                decided += 1
                per_card[cardid][winner] += 1
                seen[cardid] = winner
        per_reviewer_card[who] = seen
        integrity[who] = (ipass, itot)

    models = sorted(wins) or sorted({m["model"] for m in reveal.values()})
    lines: list[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    out("# Blind panel tally")
    out()
    out(f"reviewers: {len(per_reviewer_card)}  ({', '.join(per_reviewer_card) or '-'})")
    out(f"decided votes: {decided}   ties: {ties}")
    if len(manifest_hashes) > 1:
        out(f"WARNING: exports span multiple builds {sorted(manifest_hashes)} -- "
            "layouts differ, tally still valid per vote")
    out()

    out("## Preference (pooled)")
    for m in models:
        frac = wins[m] / decided if decided else 0
        out(f"  {m:<24} {wins[m]:>3}  ({frac*100:4.1f}% of decided)")
    if len(models) == 2 and decided:
        a, b = models
        lead, k = (a, wins[a]) if wins[a] >= wins[b] else (b, wins[b])
        p = sign_test_p(k, decided)
        frac = k / decided
        out()
        out(f"  sign test: {k}/{decided} for {lead}, two-sided p = {p:.4f}")
        verdict = (f"panel prefers {lead}"
                   if frac >= WIN_FRAC and decided >= MIN_DECIDED
                   else "no panel preference (rule: >=70% of decided and >=10 decided)")
        out(f"  verdict: {verdict}")
    out()

    out("## Per card")
    for cardid in sorted(per_card):
        row = per_card[cardid]
        parts = [f"{m} {row[m]}" for m in models if row.get(m)]
        if row.get("tie"):
            parts.append(f"tie {row['tie']}")
        lean = ""
        if len(models) == 2:
            wa, wb = row.get(models[0], 0), row.get(models[1], 0)
            if wa != wb:
                lean = f"  -> {models[0] if wa > wb else models[1]}"
        out(f"  {cardid:<22} {', '.join(parts)}{lean}")
    out()

    reviewers = list(per_reviewer_card)
    if len(reviewers) >= 2:
        out("## Inter-reviewer agreement (decided cards in common)")
        for i in range(len(reviewers)):
            for j in range(i + 1, len(reviewers)):
                ra, rb = reviewers[i], reviewers[j]
                ca, cb = per_reviewer_card[ra], per_reviewer_card[rb]
                common = [c for c in ca if c in cb and ca[c] != "tie" and cb[c] != "tie"]
                if not common:
                    out(f"  {ra} vs {rb}: no decided cards in common")
                    continue
                same = sum(1 for c in common if ca[c] == cb[c])
                out(f"  {ra} vs {rb}: {same}/{len(common)} agree ({same/len(common)*100:.0f}%)")
        out()

    out("## Integrity checks (identical pairs)")
    for who, (p_, t_) in integrity.items():
        if t_:
            flag = "" if p_ == t_ else "   <-- inattentive?"
            out(f"  {who}: {p_}/{t_} answered 'no preference'{flag}")
        else:
            out(f"  {who}: no integrity cards in export")

    if skipped:
        out()
        out("## Skipped")
        for s in skipped:
            out(f"  {s}")

    (PANEL / "tally.md").write_text("\n".join(lines) + "\n")
    out()
    out(f"written: {PANEL / 'tally.md'}")


if __name__ == "__main__":
    main()
