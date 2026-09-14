"""
Carry the region and the streaming explanation onto the COMMITTED client
report without the raw runs.

WHY THIS EXISTS. `voice/dashboard/index.html` is generated from `voice/runs/`,
and on 2026-09-14 the runs were on another machine. Ravi asked for the model
region and the streaming method on the page that day. Both are facts about
the configuration and the instrument, not numbers a run produced, so they can
be added without inventing anything:

  - region rows come from `configs/models.yaml` through the same registry the
    runner reads, labelled as read back from the config (the runs on the page
    predate the manifest field, which is exactly what a re-export would say);
  - the streaming explanation is the template's own `_streaming_how.j2`,
    rendered as-is.

The markup mirrors `client.html.j2` so the next real re-export replaces this
patch with the same sections. Idempotent: a page already patched is left
alone. Run from `voice/`:

    .venv/bin/python scratch/patch_committed_report.py [dashboard/index.html]
"""

from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from runner.models import load_registry  # noqa: E402

MARK = "<!-- patched 2026-09-14: region + streaming method; superseded by the next re-export -->"


def _rows(reg, enabled_ids: list[str]) -> list[dict]:
    by = {m.id: m for m in reg.models}
    out = []
    for mid in enabled_ids:
        m = by[mid]
        out.append({"model_id": mid, "served_from": m.served_from, "region": m.region or "—",
                    "region_note": m.region_note})
    return out


def _table(rows, judge, judge_name, asr, asr_name) -> str:
    tr = "".join(
        f'  <tr>\n    <td class="metric"><code>{escape(r["model_id"])}</code></td>\n'
        f'    <td><b>{escape(r["served_from"])}</b></td>\n'
        f'    <td class="n"><code>{escape(r["region"])}</code></td>\n'
        f'    <td class="note-cell">{escape(r["region_note"])}</td>\n  </tr>\n'
        for r in rows)
    tr += (f'  <tr>\n    <td class="metric">judge <code>{escape(judge_name)}</code></td>\n'
           f'    <td><b>{escape(judge.served_from)}</b></td>\n'
           f'    <td class="n"><code>{escape(judge.region or "—")}</code></td>\n'
           f'    <td class="note-cell">{escape(judge.region_note)}</td>\n  </tr>\n')
    tr += (f'  <tr>\n    <td class="metric">transcriber <code>{escape(asr_name)}</code></td>\n'
           f'    <td><b>{escape(asr.served_from)}</b></td>\n'
           f'    <td class="n"><code>{escape(asr.region or "—")}</code></td>\n'
           f'    <td class="note-cell">{escape(asr.region_note)}</td>\n  </tr>\n')
    return (
        f'{MARK}\n<h2 id="served">Where each model was served from</h2>\n'
        '<div class="mtable"><div class="mscroll"><table>\n'
        '  <thead><tr><th>Model</th><th>Served from</th><th>Region</th><th>What that means</th></tr></thead>\n'
        f'  <tbody>\n{tr}  </tbody>\n</table></div></div>\n'
        '<p class="one dim">Read back from the study\'s model configuration for runs\n'
        'that predate the field in the run record; every run from 14 September 2026 records it itself.\n'
        'The Gemini arm is called at a Vertex AI regional endpoint we choose; the ElevenLabs arm is a direct\n'
        'API call with no region parameter, so its region is the vendor\'s default.</p>\n\n'
    )


NOTE_CSS = ".note-cell{font-size:12.5px;color:var(--body);max-width:52ch;line-height:1.45}\n"
HOW_MARK = "<!-- streaming explainer rendered from _streaming_how.j2 -->"

# The dense paragraph the first export carried under the first-audio panel,
# and the collapsed block the first patch added. Both give way to the
# explainer.
DENSE_NOTE = re.compile(
    r'<p class="one dim"><b>First audio is the time to the first chunk of sound</b>.*?</p>\n', re.S)
OLD_DETAILS = re.compile(r'<details class="how">.*?</details>\n', re.S)
OLD_DETAILS_CSS = re.compile(r"details\.how[^\n]*\n(?:details\.how[^\n]*\n)*")
PANEL_ANCHOR = "<h2>Scenario by scenario</h2>"
NEW_NOTE = ('<p class="one dim">Medians are over both passes. Only scenarios that set a first-audio '
            'ceiling are streamed, so only those appear here.</p>\n')


def _how_css() -> str:
    """The `.how` rules, lifted from the client stylesheet so they cannot drift."""
    css = (HERE / "runner" / "templates" / "_client_assets.j2").read_text(encoding="utf-8")
    m = re.search(r"(/\* The two-stopwatches explainer.*?)\n\.notes\{", css, re.S)
    if not m:
        raise SystemExit("could not find the .how rules in _client_assets.j2")
    return m.group(1) + "\n"


def _streaming_from_page(html: str) -> dict:
    """
    The first-audio panel's own numbers, read back off the page.

    Per model: median first audio and median whole call, from the first
    table under the panel. Per scenario: one entry per row of the second
    table. Nothing is computed that the page does not already print.
    """
    i, j = html.find("<h2>Time to first audio</h2>"), html.find(PANEL_ANCHOR)
    if i < 0 or j < 0:
        return {"per_model": [], "per_scenario": []}
    seg = html[i:j]
    per_model = []
    for m in re.finditer(
            r'<td class="metric">([^<]+)</td>\s*<td class="n">(\d+) ms</td>\s*'
            r'<td class="n">[^<]*</td>\s*<td class="n">(?:([\d.]+)s|&mdash;)</td>\s*'
            r'<td class="n">(\d+) / (\d+)</td>', seg):
        per_model.append({"model_id": m.group(1).strip(), "p50": int(m.group(2)),
                          "whole_p50": int(float(m.group(3)) * 1000) if m.group(3) else None,
                          "passed": int(m.group(4)), "n": int(m.group(5))})
    per_scenario = re.findall(r'<td class="metric"><code>([^<]+)</code></td>\s*<td class="n">', seg)
    return {"per_model": per_model, "per_scenario": per_scenario}


def _patch_streaming(html: str) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    streaming = _streaming_from_page(html)
    env = Environment(loader=FileSystemLoader(str(HERE / "runner" / "templates")),
                      autoescape=select_autoescape(["html", "j2"]))
    block = HOW_MARK + "\n" + env.get_template("_streaming_how.j2").render(streaming=streaming).lstrip()
    html = OLD_DETAILS.sub("", html)
    html = re.sub(r'<!-- streaming explainer rendered from _streaming_how\.j2 -->\n<div class="how".*?\n</div>\n',
                  "", html, flags=re.S)
    html = OLD_DETAILS_CSS.sub("", html)
    if DENSE_NOTE.search(html):
        html = DENSE_NOTE.sub(NEW_NOTE + block, html, count=1)
    else:
        assert html.count(PANEL_ANCHOR) == 1, "scenario anchor"
        html = html.replace(PANEL_ANCHOR, block + "\n" + PANEL_ANCHOR)
    if "/* The two-stopwatches explainer" not in html:
        html = html.replace("</style>", _how_css() + "</style>", 1)
    return html


def patch(page: Path) -> bool:
    html = page.read_text(encoding="utf-8")
    if MARK in html:
        # Region already there; refresh only the streaming explainer, which
        # is re-rendered from the template each time.
        page.write_text(_patch_streaming(html), encoding="utf-8")
        print(f"{page}: region already present; streaming explainer refreshed from the template")
        return True
    reg = load_registry(HERE / "configs")
    judge, asr = reg.judges["voice"], reg.asr

    # The two arms on the page, Gemini first, taken from the page itself so
    # the patch cannot name an arm the report does not compare.
    title = re.search(r"<h1>([^<]+) vs ([^<]+)</h1>", html)
    if not title:
        raise SystemExit("could not find the '<h1>A vs B</h1>' heading on the page")
    arms = [title.group(1).strip(), title.group(2).strip()]
    known = {m.id for m in reg.models}
    missing = [a for a in arms if a not in known]
    if missing:
        raise SystemExit(f"arm(s) {missing} are not in configs/models.yaml - cannot state a region for them")
    rows = _rows(reg, arms)
    judge_name = re.search(r"<b>Judge:</b> <code>([^<]+)</code>", html)
    judge_name = judge_name.group(1) if judge_name else judge.provider_model
    asr_name = re.search(r"Transcribed locally by <code>([^<]+)</code>", html)
    asr_name = asr_name.group(1) if asr_name else asr.provider_model

    # 1. Hero one-liner, before the Overall line.
    hero = ('  <p class="one"><b>Served from:</b>\n    '
            + "; ".join(f'<code>{escape(r["model_id"])}</code> — {escape(r["served_from"])}' for r in rows)
            + '.\n    Detail under <a href="#served">where each model was served from</a>.</p>\n')
    anchor = '  <p class="one lead"><b>Overall:</b>'
    assert html.count(anchor) == 1, "hero anchor"
    html = html.replace(anchor, hero + anchor)

    # 2. The table, before the metric table.
    anchor = "<h2>Every metric, both models</h2>"
    assert html.count(anchor) == 1, "metric-table anchor"
    html = html.replace(anchor, _table(rows, judge, judge_name, asr, asr_name) + anchor)

    # 3. The streaming explainer, rendered from the template itself against
    #    the numbers the page already prints.
    html = _patch_streaming(html)

    # 4. The footer note.
    li = ('  <li><b>Served from:</b> '
          + "; ".join(f'<code>{escape(r["model_id"])}</code> {escape(r["served_from"])}' for r in rows)
          + f'; the judge {escape(judge.served_from)}.\n  Read back from the model configuration for runs '
            'that predate the field in the run record.</li>\n')
    anchor = "  <li><b>The judge is <code>"
    assert html.count(anchor) == 1, "footer anchor"
    html = html.replace(anchor, li + anchor)

    # 5. The stylesheet rules the new blocks use, appended inside the page's own <style>.
    html = html.replace("</style>", NOTE_CSS + "</style>", 1)

    page.write_text(html, encoding="utf-8")
    print(f"{page}: patched - {len(rows)} arms, judge, transcriber; streaming method added")
    return True


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dashboard" / "index.html"
    patch(target)
