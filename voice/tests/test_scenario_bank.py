"""
Every scenario on disk must be SATISFIABLE by a perfect reading of its own
script.

This file exists because of a real failure. On 2026-09-02, voi-ret-01 sent
"sixteen gigabytes of RAM", the ASR wrote "16 GB", and four must_say checks
failed on BOTH models identically - the signature of an instrument fault
wearing a model fault's clothes. The fix was in the normalizer, but nothing
stopped the next scenario from carrying the same defect.

So: for every scenario in the repo, take its own script as a hypothetical
perfect transcript and assert that every deterministic check would PASS. A
check that cannot pass a perfect reading is not strict, it is broken - it
reports a model failure that no model could have avoided.

Offline, no key, no network, no audio.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from runner.checks import extract_digit_sequence, longest_digit_run
from runner.normalize import normalize
from runner.scenarios import ScenarioError, load_scenarios

ROOT = Path(__file__).resolve().parent.parent
# Every directory that holds real scenarios - the default run path, the
# held-back demo set, and the real-use-case bank under it.
# `blocked/` is OUT of the default run path on purpose - those scenarios
# cannot be measured yet and must not quietly generate cells that look
# scored. They are still validated here, so they are correct the day the
# capability they wait on lands.
SCENARIO_ROOTS = [ROOT / "scenarios", ROOT / "demo" / "scenarios",
                  ROOT / "blocked" / "scenarios"]

# A human read sits inside this band. Copy whose length and duration target
# imply something outside it is testing the copywriting, not the model.
MIN_WPM, MAX_WPM = 90.0, 260.0


def all_scenarios():
    out = []
    for root in SCENARIO_ROOTS:
        if root.exists():
            out.extend(load_scenarios(root, modality="voice"))
    return sorted(out, key=lambda s: s.id)


SCENARIOS = all_scenarios()
IDS = [s.id for s in SCENARIOS]


def test_the_bank_is_not_empty():
    assert SCENARIOS, "no scenarios loaded - the paths in SCENARIO_ROOTS are wrong"


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_every_must_say_is_satisfiable_by_its_own_script(s):
    spoken = normalize(s.text)
    for phrase in s.checks.get("must_say") or []:
        need = normalize(str(phrase))
        assert need in spoken, (
            f"{s.id}: must_say {phrase!r} normalizes to {need!r}, which does not "
            f"appear in a perfect reading of the script. No model can pass this."
        )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_every_must_say_any_group_is_satisfiable_by_its_own_script(s):
    """At least one spelling in each alternation must be the one the script
    actually uses - otherwise the group is unsatisfiable like a bare
    must_say, just less visibly."""
    spoken = normalize(s.text)
    for group in s.checks.get("must_say_any") or []:
        options = group if isinstance(group, (list, tuple)) else [group]
        assert any(normalize(str(o)) in spoken for o in options), (
            f"{s.id}: none of {list(options)} appears in a perfect reading of "
            f"the script. No model can pass this."
        )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_no_must_not_say_is_already_in_the_script(s):
    spoken = normalize(s.text)
    for phrase in s.checks.get("must_not_say") or []:
        avoid = normalize(str(phrase))
        assert avoid not in spoken, (
            f"{s.id}: must_not_say {phrase!r} is IN the script. Every model would "
            f"fail it for doing exactly what it was asked to do."
        )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_digit_gates_are_satisfiable_by_their_own_script(s):
    want = s.checks.get("must_say_digits")
    if want is None:
        pytest.skip("no digit gate")
    expected = "".join(c for c in str(want) if c.isdigit())
    heard = extract_digit_sequence(s.text)
    assert expected in heard, (
        f"{s.id}: must_say_digits {expected!r} is not recoverable from the script, "
        f"which yields {heard!r}."
    )


def _as_an_asr_would_write_it(script: str) -> str:
    """
    Collapse spaced single digits into a numeral: "1 8 to 1 4" -> "18 to 14".

    A script writes digits spaced so the model reads them out; an ASR writes
    what it hears back as a NUMERAL. Checking a gate only against the spaced
    script therefore proves nothing about the transcript it will actually be
    measured against.
    """
    return re.sub(r"\b(\d)(?:\s+(\d))+\b", lambda m: m.group(0).replace(" ", ""), script)


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_digit_gates_survive_the_asr_writing_a_numeral(s):
    """
    The trap this caught, for real, in vr-game-02. A score is spoken
    "fourteen", and the digit extractor only recovers digit-BY-digit
    readings - it reconstructs a twelve-digit reference perfectly and cannot
    see a two-digit number at all. The scenario asserted "1814", passed the
    script-versus-itself check because the script wrote "1 8 to 1 4", and
    would have failed every correct reading in production.

    Rule this encodes: below four digits, must_say_digits is the wrong
    instrument - use a phrase.
    """
    want = s.checks.get("must_say_digits")
    if want is None:
        pytest.skip("no digit gate")
    expected = "".join(c for c in str(want) if c.isdigit())
    heard = extract_digit_sequence(_as_an_asr_would_write_it(s.text))
    assert expected in heard, (
        f"{s.id}: must_say_digits {expected!r} is unrecoverable once the ASR "
        f"writes the number as a numeral (script yields {heard!r}). A run of "
        f"fewer than four digits normalizes to a cardinal - 'fourteen', not "
        f"'one four' - and cannot be extracted. Use must_say with a phrase."
    )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_the_script_does_not_itself_break_its_digit_run_limit(s):
    limit = s.checks.get("max_digit_run")
    if limit is None:
        pytest.skip("no digit-run limit")
    longest = longest_digit_run(s.text)
    assert longest <= int(limit), (
        f"{s.id}: the script itself contains a {longest}-digit run against a "
        f"max_digit_run of {limit} - the disclosure gate fails on the input."
    )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_duration_targets_imply_a_humanly_possible_read(s):
    """
    A 40-word line in a 3-second window cannot be delivered by anything. If a
    duration target implies a rate outside the human band, the scenario is
    unpassable by construction and its failures would say nothing about the
    models.
    """
    spec = s.checks.get("trimmed_duration_s") or s.checks.get("duration_s")
    if not isinstance(spec, dict):
        pytest.skip("no duration target")
    words = len(normalize(s.text).split())
    # The slowest legal read is the longest window; the fastest is the shortest.
    slowest = words / (float(spec["max"]) / 60.0)
    fastest = words / (float(spec["min"]) / 60.0)
    assert slowest <= MAX_WPM and fastest >= MIN_WPM, (
        f"{s.id}: {words} words in {spec} implies {slowest:.0f}-{fastest:.0f} wpm; "
        f"a human read is {MIN_WPM}-{MAX_WPM} wpm."
    )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_a_declared_speech_rate_agrees_with_the_duration_window(s):
    """The two gates are the same physics in two vocabularies. They must not
    contradict each other, or a scenario is unpassable however well it reads."""
    rate = s.checks.get("speech_rate_wpm")
    dur = s.checks.get("trimmed_duration_s") or s.checks.get("duration_s")
    if not isinstance(rate, dict) or not isinstance(dur, dict):
        pytest.skip("not both declared")
    words = len(normalize(s.text).split())
    rate_lo_s = words / (float(rate["max"]) / 60.0)   # fastest legal rate -> shortest read
    rate_hi_s = words / (float(rate["min"]) / 60.0)
    assert rate_lo_s <= float(dur["max"]) and rate_hi_s >= float(dur["min"]), (
        f"{s.id}: rate gate allows a {rate_lo_s:.1f}-{rate_hi_s:.1f}s read but the "
        f"duration gate demands {dur} - no read satisfies both."
    )


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_every_scenario_says_what_good_looks_like(s):
    assert s.expected.strip(), f"{s.id}: empty `expected` - the judge has no brief"
    assert s.text.strip(), f"{s.id}: empty script"


def test_a_scenario_cannot_silently_declare_ignored_weights(tmp_path):
    """
    `weights` was validated (must sum to 1.0) and then never read - an author
    could be told their re-weighting was correct while the run was scored on
    something else. It refuses until the scoring path reads it.
    """
    from runner.scenarios import ScenarioError, load_scenarios

    f = tmp_path / "s.yaml"
    f.write_text(
        "id: x\n"
        "modality: voice\n"
        "task: text_to_speech\n"
        "input:\n"
        "  script: hello there\n"
        "expected: says hello\n"
        "weights: {text_accuracy: 0.5, pronunciation: 0.5}\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError, match="does not read them yet"):
        load_scenarios(f)


# --------------------------------------------------------------------------
# Variants and group gates. Added 2026-09-03: six NPC voices, or one character
# in three languages, is ONE comparison in the workbook but six or three
# separate generations. A scenario declaring `variants:` expands at load time
# into one Scenario per variant so matrix, paths, telemetry and the dashboard
# keep working on distinct ids.
# --------------------------------------------------------------------------

VARIANT_YAML = """
id: t-cast
modality: voice
task: styled_tts
title: "Cast"
expected: "Distinguishable characters."
params: {voice: male_mid_neutral}
checks:
  duration_s: {min: 1.0, max: 20.0}
  speaker_distinct: {max_cosine: 0.80}
variants:
  - id: alpha
    script: "Supplies are short and the gate closes at dusk."
  - id: beta
    script: "Movement on the ridge, two of them."
    params: {voice: female_mid_warm}
"""


def _load_yaml(tmp_path, text):
    f = tmp_path / "s.yaml"
    f.write_text(text, encoding="utf-8")
    return load_scenarios(f, modality="voice")


def test_a_variant_scenario_expands_into_one_scenario_per_variant(tmp_path):
    out = _load_yaml(tmp_path, VARIANT_YAML)
    assert [s.id for s in out] == ["t-cast#alpha", "t-cast#beta"]
    assert all(s.variant_of == "t-cast" for s in out)
    # A per-variant params override wins over the shared block.
    assert out[0].params["voice"] == "male_mid_neutral"
    assert out[1].params["voice"] == "female_mid_warm"


def test_variants_do_not_share_a_hash(tmp_path):
    """
    Two variants are different INPUTS. Sharing a hash would make the dashboard
    treat them as repeats of each other and report the difference between two
    characters as this machine's noise floor.
    """
    a, b = _load_yaml(tmp_path, VARIANT_YAML)
    assert a.scenario_hash != b.scenario_hash


def test_a_group_gate_is_lifted_out_of_the_per_clip_checks(tmp_path):
    """
    `speaker_distinct` describes the SET. Left in `checks` it would reach
    run_checks, which gates one file and cannot see a property that only
    several files together have.
    """
    a, _ = _load_yaml(tmp_path, VARIANT_YAML)
    assert "speaker_distinct" not in a.checks
    assert a.group_checks == {"speaker_distinct": {"max_cosine": 0.80}}
    assert "duration_s" in a.checks


def test_a_group_gate_with_one_variant_is_refused(tmp_path):
    """One clip cannot disagree with itself, so the scenario is a mistake."""
    bad = VARIANT_YAML.split("  - id: beta")[0]
    with pytest.raises(ScenarioError, match="at least two"):
        _load_yaml(tmp_path, bad)


def test_a_variant_without_an_id_is_refused(tmp_path):
    text = VARIANT_YAML.replace("  - id: alpha\n", "  - \n")
    with pytest.raises(ScenarioError, match="no `id`"):
        _load_yaml(tmp_path, text)


def test_duplicate_variant_ids_are_refused(tmp_path):
    text = VARIANT_YAML.replace("  - id: beta", "  - id: alpha")
    with pytest.raises(ScenarioError, match="duplicate variant id"):
        _load_yaml(tmp_path, text)


def test_a_plain_scenario_still_needs_a_script(tmp_path):
    text = VARIANT_YAML.split("variants:")[0].replace(
        "  speaker_distinct: {max_cosine: 0.80}\n", "")
    with pytest.raises(ScenarioError, match="input.script"):
        _load_yaml(tmp_path, text)


@pytest.mark.parametrize("s", SCENARIOS, ids=IDS)
def test_no_script_ends_with_whisper_boilerplate(s):
    """
    The tail-hallucination guard strips subtitle boilerplate from the end of a
    transcript. That is only safe while no scenario legitimately ENDS with one
    of those phrases - otherwise the harness would quietly edit real evidence
    out of a correct reading.
    """
    from runner.asr import TAIL_HALLUCINATIONS

    import re as _re
    last = [p for p in _re.split(r"(?<=[.!?])\s+", normalize(s.text)) if p.strip()]
    if not last:
        pytest.skip("no sentences")
    tail = _re.sub(r"\s+", " ", last[-1]).strip()
    assert tail not in TAIL_HALLUCINATIONS, (
        f"{s.id} ends with {tail!r}, which the ASR guard strips as boilerplate - "
        f"the harness would delete a real line from a correct reading"
    )
