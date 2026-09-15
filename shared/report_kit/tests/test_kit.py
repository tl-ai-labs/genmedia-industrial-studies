"""The report kit's own tests — and the checks that keep every lane on it.

Run from shared/report_kit:  python -m pytest
"""
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import report_kit as kit
from report_kit.formatting import make_filters

REPO = Path(__file__).resolve().parents[3]
LANES = ("image", "video", "voice")
DEFAULT_HOOKS = kit.KIT_TEMPLATES / "kit" / "lane_hooks_default.j2"


def _macros(path: Path) -> dict:
    """name -> argument list, read from a template's {% macro %} lines."""
    return {m.group(1): [a.strip().split("=")[0] for a in m.group(2).split(",") if a.strip()]
            for m in re.finditer(r"{%-?\s*macro\s+(\w+)\((.*?)\)\s*-?%}", path.read_text())}


# ------------------------------------------------------ lanes stay on the kit


@pytest.mark.parametrize("lane", LANES)
def test_every_lane_defines_every_hook_with_the_same_arguments(lane):
    hooks = REPO / lane / "runner" / "templates" / "lane_hooks.j2"
    assert hooks.exists(), f"{lane}: runner/templates/lane_hooks.j2 is missing"
    want, have = _macros(DEFAULT_HOOKS), _macros(hooks)
    missing = sorted(set(want) - set(have))
    assert not missing, f"{lane}: hooks not defined: {missing}"
    for name, args in want.items():
        assert have[name] == args, f"{lane}: {name}{tuple(have[name])} should be {name}{tuple(args)}"


@pytest.mark.parametrize("lane", LANES)
def test_no_lane_keeps_its_own_page_templates_or_design_tokens(lane):
    """Page chrome, sections, styles and scripts live in the kit only. A lane
    template may add markup through hooks, never a second design system."""
    tdir = REPO / lane / "runner" / "templates"
    for f in tdir.glob("*.j2"):
        text = f.read_text()
        assert "<!doctype" not in text.lower(), f"{f}: a lane never renders its own page"
        assert ":root" not in text, f"{f}: design tokens belong in kit/_styles.j2"
        # colours come from var(--token); a literal hex colour is a second palette
        colours = re.findall(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"&#\d+;|href=\"#[^\"]*\"|id=\"[^\"]*\"", "", text))
        colours = [c for c in colours if not re.fullmatch(r"#\d+", c)]   # "#3" in prose is not a colour
        assert not colours, f"{f}: hard-coded colours {colours}; use var(--…) tokens"
    for old in ("_assets.j2", "_sections.j2", "report.html.j2", "report-client.html.j2",
                "combined.html.j2", "_client_assets.j2", "client.html.j2", "dashboard.html.j2"):
        assert not (tdir / old).exists(), f"{lane}: {old} duplicates the kit — delete it"


def test_the_kit_import_shim_is_identical_in_every_lane():
    copies = {lane: (REPO / lane / "runner" / "_report_kit.py").read_text() for lane in LANES}
    assert len(set(copies.values())) == 1, "runner/_report_kit.py differs between lanes"


@pytest.mark.parametrize("lane", LANES)
def test_lane_report_code_does_not_rebuild_the_environment(lane):
    """Filters, globals and loaders come from report_kit.make_env — a lane that
    builds its own Environment is how formats drift apart."""
    runner = REPO / lane / "runner"
    for f in runner.glob("*.py"):
        text = f.read_text()
        if "templates" in text and "Environment(" in text:
            pytest.fail(f"{f}: builds its own jinja Environment; use report_kit.make_env")


# --------------------------------------------------------------- formatting


def test_quality_is_points_internally_and_percent_for_the_client():
    i, c = make_filters(client=False), make_filters(client=True)
    assert i["q"](8.0) == "8.00" and c["q"](8.0) == "80.0%"
    # short badges never print two different scores alike
    assert c["q"](9.075, True) == "90.75%" and c["q"](9.1, True) == "91%" and c["q"](10.0, True) == "100%"
    assert i["q"](9.075, True) == "9.075" and i["q"](10.0, True) == "10"
    assert c["qd"](0.925, True) == "+9.25 pp" and c["qd"](0.3, True) == "+3 pp"
    assert i["qd"](0.925, True) == "+0.925" and i["qd"](-0.5) == "-0.50"
    assert i["q"](None) == "—"


def test_win_percent_keeps_a_decimal_only_when_it_has_one():
    wp = make_filters()["win_pct"]
    assert (wp(62.5), wp(37.5), wp(50.0), wp(33.3), wp(None)) == ("62.5%", "37.5%", "50%", "33.3%", "—")


def test_units_print_the_same_everywhere():
    f = make_filters()
    assert f["usd"](134000) == "$0.1340" and f["s"](25500) == "25.5s" and f["ms"](412) == "412 ms"
    assert f["pct"](0.5) == "50%" and f["tasktitle"]("text_to_video") == "Text to video"


# ------------------------------------------------------------ comparisons


def test_gemini_first_puts_the_google_arm_left():
    assert kit.gemini_first(["z-gpt", "a-gemini-pro"], {"a-gemini-pro": "Google", "z-gpt": "OpenAI"}) \
        == ["a-gemini-pro", "z-gpt"]
    assert kit.gemini_first(["a-seedance", "z-omni"], {"z-omni": "Google", "a-seedance": "ByteDance"}) \
        == ["z-omni", "a-seedance"]
    assert kit.gemini_first(["model-b", "model-a"], {}) == ["model-a", "model-b"]


def _m(mean, lat, worst=5.0, failed=0):
    return {"mean": mean, "worst": worst, "wtl": "1-0-0", "judged_n": 1, "eligible": 1,
            "below_5": 0, "failed": failed, "gen_cost_per_scenario_usd": 0.1,
            "judge_cost_per_scenario_usd": 0.01, "latency_min_ms": lat, "latency_p50_ms": lat,
            "latency_max_ms": lat, "success_rate": 1.0, "mean_attempts": 1.0}


def test_metric_row_delta_follows_each_metric_direction():
    rows = {r["key"]: r for r in kit.metric_rows({"g": _m(8.0, 3000), "r": _m(7.0, 5000)}, ["g", "r"], "image")}
    assert rows["mean"]["delta"] == pytest.approx(1.0) and rows["mean"]["delta_class"] == "up"
    assert rows["lat_p50"]["delta"] == pytest.approx(-2000) and rows["lat_p50"]["delta_class"] == "up"
    assert rows["mean"]["hi"] and rows["lat_p50"]["hi"]
    slower = {r["key"]: r for r in kit.metric_rows({"g": _m(8.0, 9000), "r": _m(7.0, 5000)}, ["g", "r"], "image")}
    assert slower["lat_p50"]["delta_class"] == "down"
    tied = {r["key"]: r for r in kit.metric_rows({"g": _m(8.0, 3000), "r": _m(8.0, 3000)}, ["g", "r"], "image")}
    assert tied["mean"]["delta_class"] == ""
    solo = {r["key"]: r for r in kit.metric_rows({"g": _m(8.0, 3000)}, ["g"], "image")}
    assert solo["mean"]["delta"] is None


def test_relative_difference_is_computed_against_the_rival():
    rows = {r["key"]: r for r in kit.metric_rows({"g": _m(8.0, 20000), "r": _m(7.0, 40000)}, ["g", "r"], "clip")}
    assert rows["lat_p50"]["delta_rel"] == pytest.approx(-50.0)
    zero = {r["key"]: r for r in kit.metric_rows({"g": _m(8.0, 20000), "r": _m(7.0, 0)}, ["g", "r"], "clip")}
    assert zero["lat_p50"]["delta_rel"] is None


def test_common_rows_are_fixed_and_lane_rows_follow_them():
    extra = [{"key": "wer", "label": "Worst word error rate", "better": "lower", "unit": "ratio",
              "get": lambda m: 0.1}]
    keys = [r["key"] for r in kit.metric_rows({"g": _m(8, 1), "r": _m(7, 2)}, ["g", "r"], "clip", extra)]
    assert keys == ["mean", "worst", "wtl", "judged", "below_5", "gen_cost", "judge_cost",
                    "lat_min", "lat_p50", "lat_max", "success", "attempts", "wer"]
    labels = {r["key"]: r["label"] for r in kit.metric_rows({"g": _m(8, 1)}, ["g"], "clip")}
    assert labels["gen_cost"] == "Cost per clip"
    # a failure is never silent: the Failed row appears once anything failed, client-visible
    failed = {r["key"]: r for r in kit.metric_rows({"g": _m(8, 1), "r": _m(7, 2, failed=2)}, ["g", "r"], "clip")}
    assert "failed" in failed and failed["failed"]["internal_only"] is False
    assert {"judged", "below_5", "judge_cost", "success", "attempts"} == kit.INTERNAL_ROWS


def test_rollup_win_percent_counts_only_compared_scenarios():
    row = {"n": 8, "compared": 3, "models": {
        "g": {"scores": [10.0, 9.0, 10.0, 10.0], "wins": 1},
        "r": {"scores": [10.0, 8.2, 10.0, 5.4], "wins": 0}}}
    kit.finish_rollup(row)
    assert row["models"]["g"]["win_pct"] == 33.3 and row["models"]["r"]["win_pct"] == 0.0
    assert row["lead_mean"] == "g" and row["lead_wins"] == "g"
    row = {"n": 10, "compared": 8, "models": {"g": {"scores": [9.0], "wins": 3}, "r": {"scores": [9.0], "wins": 5}}}
    kit.finish_rollup(row)
    assert (row["models"]["g"]["win_pct"], row["models"]["r"]["win_pct"]) == (37.5, 62.5)
    assert row["lead_mean"] is None and row["lead_wins"] == "r"
    row = {"n": 2, "compared": 0, "models": {"g": {"scores": [9.0], "wins": 0}}}
    kit.finish_rollup(row)
    assert row["models"]["g"]["win_pct"] is None and row["lead_wins"] is None


def test_scenario_result_and_tally_account_for_every_scenario():
    cards = lambda a, b: [{"model_id": "g", "score": a, "state": "done" if a is not None else "failed"},
                          {"model_id": "r", "score": b, "state": "done" if b is not None else "failed"}]
    ev = []
    for a, b in ((9.0, 8.0), (8.0, 8.0), (None, 7.0), (None, None)):
        w, m = kit.scenario_result(cards(a, b))
        ev.append({"cards": cards(a, b), "winner": w, "margin": m})
    assert [e["winner"] for e in ev] == ["g", None, None, None]
    assert [e["margin"] for e in ev] == [1.0, 0.0, None, None]
    t = kit.tally(ev, ["g", "r"])
    assert (t["n"], t["compared"], t["ties"], t["not_compared"]) == (4, 2, 1, 2)
    assert t["only_missing"]["g"] == 1 and t["all_missing"] == 1 and t["all_failed"]


# ---------------------------------------------------------------- rendering


def _ctx():
    models = {"g": dict(_m(8.0, 3000), wtl="1-0-0"), "r": dict(_m(7.0, 5000), wtl="0-0-1")}
    cards = [{"model_id": "g", "score": 8.0, "state": "done"}, {"model_id": "r", "score": 7.0, "state": "done"}]
    ev = [{"id": "S-1", "title": "t", "prompt": "p", "task": "text_to_image", "family": "f",
           "industry": "Ads", "winner": "g", "margin": 1.0, "g_score": 8.0, "c_score": 7.0,
           "gap": 1.0, "sources": [], "cards": cards}]
    agg = {"tasks": {"text_to_image": {"models": models, "pairs": [],
                                       "metric_rows": kit.metric_rows(models, ["g", "r"], "image")}}}
    return dict(manifest={"run_id": "run-1", "state": "reported", "created": "now"}, agg=agg,
                evidence=ev, names={"g": "Gem", "r": "Rival"}, vendors={"g": "Google", "r": "OpenAI"},
                model_order=["g", "r"], family_models=["g", "r"], duel=kit.build_duel(agg["tasks"], ["g", "r"], "image"),
                tally=kit.tally(ev, ["g", "r"]), families=kit.rollup(ev, "family"),
                industries=kit.rollup(ev, "industry"), totals={"gen_micro": 1, "judge_micro": 1},
                completion={"completed": 1, "total": 1})


def test_one_context_renders_both_audiences_with_the_audience_rules(tmp_path):
    hooks = tmp_path / "templates"
    hooks.mkdir()
    (hooks / "lane_hooks.j2").write_text(DEFAULT_HOOKS.read_text())
    lane = kit.LaneProfile(key="test", unit="image", media="image", templates=hooks)
    out = kit.render_run(lane, _ctx(), tmp_path)
    internal, client = out.read_text(), (tmp_path / "report-client.html").read_text()
    for html in (internal, client):
        assert html.startswith("<!doctype html>")
        assert ">Overall summary<" in html and '<table class="mx">' in html
        assert 'class="roll"' in html and "data-sort" in html and "report generated" in html
    assert 'class="tiles"' in internal and 'class="tiles"' not in client
    assert ">Difference<" in client and ">Difference<" not in internal
    assert "80.0%" in client and "8.00" in internal
    assert "Judge cost/scen" in internal and "Judge cost/scen" not in client
    assert "is the model's lowest rating" in client


def test_a_study_names_its_client_copy_beside_the_internal_one(tmp_path):
    hooks = tmp_path / "templates"
    hooks.mkdir()
    (hooks / "lane_hooks.j2").write_text(DEFAULT_HOOKS.read_text())
    lane = kit.LaneProfile(key="test", unit="image", media="image", templates=hooks)
    out = kit.render_study(lane, [_ctx(), _ctx()], tmp_path / "study.html")
    assert out.name == "study.html" and (tmp_path / "study-client.html").exists()
    assert "Gem vs Rival across" in out.read_text()
