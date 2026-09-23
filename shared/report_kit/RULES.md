# Report rules — image, video and voice

Every HTML report in this repo is presented by **one kit**: `shared/report_kit`.
Each lane owns its data and its media; the kit owns everything a reader sees
as *format*. If you want to change how reports look, read, or are named, you
change the kit once and all three lanes pick it up on their next render.

Reference designs: the client report follows *categorized run client report*
(2026-09-15, image tier study); the internal report follows the 2026-09-14
tier-study internal report.

## 1. Who owns what

| Concern | Owner | Where |
|---|---|---|
| Page chrome: top bar, hero, tiles, tabs | kit | `templates/kit/run.html.j2`, `combined.html.j2`, `_sections.j2` |
| Section order | kit | `_sections.j2 → report_body()` |
| Tables: metric table, head-to-head strip, family/industry rollups, evidence list, chips, footnotes | kit | `_sections.j2` |
| Colours, fonts, spacing, components | kit | `_styles.j2` (tokens in `:root`) |
| Page behaviour: filters, row filters, sort, expand, tabs, lightbox, one-player-at-a-time | kit | `_behaviour.j2` |
| Number formats | kit | `formatting.py` |
| Common metric rows, their order and labels | kit | `compare.py → metric_rows()` |
| Ordering (Gemini arm first), rollups, win %, tally, head-to-head | kit | `compare.py` |
| Internal vs client rules | kit | templates (`CLIENT`), `INTERNAL_ROWS` |
| File names | kit | `rendering.py` |
| Loading run records, scoring, verdicts | lane | `runner/report.py`, `runner/scoring.py` |
| Media markup (image, video player, audio clips) and lane measures | lane | `runner/templates/lane_hooks.j2` |
| Lane-only sections (e.g. voice streaming, human review) | lane | hooks in `lane_hooks.j2` |

## 2. How a lane renders a report

```python
from . import _report_kit as kit                      # identical file in every lane

LANE = kit.LaneProfile(key="image", unit="image", media="image",
                       templates=Path(__file__).parent / "templates")

ctx = _build_context(...)                              # lane data, kit-shaped
kit.render_run(LANE, ctx, run_dir)                     # one run   → report.html + report-client.html
kit.render_study(LANE, [ctx1, ctx2], out_path)         # many runs → <out>.html + <out>-client.html
kit.render_both(LANE, "kit/run.html.j2", ctx, internal, client)   # custom paths
html = kit.render_page(LANE, "kit/run.html.j2", ctx, client=True)  # one audience, returned
kit.write_page(html, path, guard=guard_size)          # UTF-8, optional size guard first
```

The context keys the kit reads are the ones `image/runner/report.py::_build_context`
returns: `manifest, agg, evidence, names, vendors, model_order, family_models,
duel, tally, families, industries, totals, completion, estimates, judge_meta,
judge_version, vendor_lines, params_unsupported, hidden_industries`. Build
them with the kit helpers (`metric_rows`, `build_duel`, `rollup`, `tally`,
`scenario_result`, `gemini_first`, `vendor_lines`) — never re-implement them
in a lane. A lane may add its own keys for its hooks (voice keeps them under
`ctx.voice`).

Optional keys — the only sanctioned way to change the kit's wording for a lane:

| Key | Replaces | Used by |
|---|---|---|
| `win_rule_note` `{"client": …, "internal": …}` | "won by any margin … only an identical score ties" | voice (decision band) |
| scenario `result_key` / `result_label`, `result_kinds` `{key: chip label}` | "tie" for a compared scenario with no winner | voice ("split") |
| `runs_note` | "one run per scenario" in the client kicker | voice study |
| `generation_note` | "Every output was generated once" in the client footnote | voice study |
| `industry_source_note` | "Industries come from the sheet's Industry mapping tab" | voice |
| metric extra `decimals` | whole-number ratio rows | voice worst WER |

Card hooks (`card_media`, `card_measures`, …) receive `s` and `c`, not `ctx`:
copy any page-level flag a card needs onto the card when building it.

## 3. Rules every developer follows

**Consistency**

1. A lane never renders its own page. No `<!doctype>`, no page template, no
   `_assets.j2` / `_sections.j2` copy in a lane. Pages come from `kit/`.
2. A lane adds content **only** through the hooks in
   `kit/lane_hooks_default.j2`. Every lane's `lane_hooks.j2` defines every
   hook with the same arguments; an empty macro means "nothing to add".
3. Sections appear in the kit's order: Overall summary → banners →
   *after_summary* → task tables (+ verdict, internal) → By use-case family →
   By target industry → *after_tables* → Per-scenario evidence →
   *after_evidence* → Footnotes. A hook adds a section; it never moves one.
4. No hard-coded colours or fonts in a lane. Use kit classes (`tw`, `pill`,
   `tag`, `banner`, `note`, `tnote`, `hint`, `crit`, `disc` …) and
   `var(--token)`. `extra_css` is for lane media layout only.
5. Every number goes through a kit filter: `q`, `qd`, `usd`, `s`, `ms`, `pct`,
   `win_pct`, `disp`, `tasktitle`. No inline `'%.2f'|format` for a figure
   the reader compares.
6. The common metric rows are fixed (keys, order, labels). A lane-only
   measure goes in `metric_rows(..., extra=[...])` and appears after them.

**Audiences**

7. Every report is rendered twice from **one** context builder — internal and
   client — with `render_run` / `render_study` / `render_both`. Never a flag.
   A lane whose client copy needs extra work first (voice encodes audio and
   checks the page size) may render the two audiences in separate commands,
   but both must come from the same builder (`kit_context.study_context`).
8. Client reports show quality as a percentage of the 10-point rubric and
   gaps in percentage points; internal reports show points (0-10).
9. Client reports never show: generation/judging totals, tiles, verdicts or
   "how decided", judge cost, `Judged`, `<5`, `Success`, `Attempts`, blind
   labels, raw provider errors, rubric hashes. Add a row to `INTERNAL_ROWS`
   (or `internal_only=True`) to keep it internal.
10. A failure is never silent: a scenario missing a result is "not compared"
    (its own chip), and the Failed row appears when anything failed.
11. The page states its scenario rule in each audience's units. The default
    is "only identical scores tie" ("99% vs 100% is a win"); a lane with a
    different rule (voice's decision band) states it through `win_rule_note`,
    never by editing the kit's sentence.

**Naming**

12. One run: `runs/<run-id>/report.html` and `report-client.html`.
    A study: `<name>.html` and `<name>-client.html` side by side.
    Voice's cross-run study keeps its established paths: `runs/index.html`
    (internal) and `runs/client-report/index.html` with `audio/` beside it
    (`--inline` → `runs/client-report.html`). A hosting export
    (`--out DIR`) writes `DIR/index.html`.
13. Titles read `GenMedia comparison · <lane> — <run id or study title>`;
    the top bar shows `<lane> · <run id>`.
14. Headings use the kit's words: "Overall summary", "Per-scenario
    evidence", "By use-case family", "By target industry", "Footnotes".

## 4. Making a change

| I want to… | Change | Not |
|---|---|---|
| restyle anything (colour, spacing, a table) | `kit/_styles.j2` or `_sections.j2` | a lane template |
| change how a number prints | `formatting.py` | a template |
| add/rename a metric every lane has | `compare.py → metric_rows` | a lane's `report.py` |
| add a metric only my lane has | `metric_rows(..., extra=[…])` in my `report.py` | `compare.py` |
| show my lane's media differently | my `lane_hooks.j2` (`card_media`, `source_media`, …) | `_sections.j2` |
| add a section only my lane has | a hook in my `lane_hooks.j2` | a new page template |
| add a new hook point | `lane_hooks_default.j2` + `_sections.j2` + **all three** `lane_hooks.j2` | one lane |
| hide something from clients | `INTERNAL_ROWS` or `{% if not CLIENT %}` in the kit | delete it from one lane |

A kit change is reviewed by all three lane owners, because it changes all
three lanes' reports. Before merging:

```bash
cd shared/report_kit && ../../image/.venv/bin/python -m pytest   # kit + lane-conformance checks
cd image && .venv/bin/python -m pytest
cd video && PYTHONPATH=. ../image/.venv/bin/python -m pytest
cd voice && <voice venv>/bin/python -m pytest       # voice has its own deps (see voice/README.md)
```

Then re-render one report per lane and look at both copies.

`tests/test_kit.py` enforces rules 1, 2, 4 and the shared shim automatically:
every lane defines every hook, no lane carries page templates or colours, the
`_report_kit.py` shim is byte-identical, and no lane builds its own Jinja
environment.
