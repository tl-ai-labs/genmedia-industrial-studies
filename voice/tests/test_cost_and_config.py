"""
Per-provider billing units, and the config that declares them.

Billing differs per provider and this is where that is proven honest: the
same 600-character script must cost the right amount whether the vendor
bills tokens, characters-per-thousand, characters-per-million, or minutes.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from runner.cost import Usage, compute_cost, fmt_usd
from runner.models import ConfigError, Price, load_registry, preflight
from runner.scenarios import ScenarioError, load_scenarios

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
# Sample scenarios live under tests/ so they can never enter a real run matrix;
# scenarios/ holds only what we have been asked to measure.
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "scenarios"


def price(**kw) -> Price:
    kw.setdefault("source", "https://example.test/pricing")
    kw.setdefault("as_of", "2026-08-31")
    return Price(**kw)


# --------------------------------------------------------------------------
# Billing units
# --------------------------------------------------------------------------


def test_per_1k_chars_matches_the_plan_worked_example():
    """ElevenLabs at $0.05/1k chars, 600-character script -> $0.030."""
    cost = compute_cost(
        Usage(characters=600), price(unit="per_1k_chars", usd=0.05), "elevenlabs"
    )
    assert cost.micro_usd == 30_000
    assert cost.usd == pytest.approx(0.030)
    assert cost.usage_exact is True
    assert "chars_sent=600" in cost.basis


def test_per_1m_chars_prices_tts_1():
    cost = compute_cost(Usage(characters=600), price(unit="per_1m_chars", usd_per_1m=15.00), "tts-1")
    assert cost.micro_usd == 9_000  # 600 / 1e6 * $15
    assert cost.usage_exact is True


def test_per_minute_prices_from_measured_duration_not_requested():
    cost = compute_cost(Usage(audio_seconds=38.0), price(unit="per_minute", usd=0.003), "asr")
    assert cost.micro_usd == 1_900  # 38/60 * $0.003
    assert "seconds=38.000" in cost.basis


def test_reported_tokens_are_used_verbatim_and_marked_exact():
    cost = compute_cost(
        Usage(reported=True, input_tokens=150, audio_out_tokens=950),
        price(unit="tokens", usd_in_per_1m=0.60, usd_audio_out_per_1m=12.00),
        "gemini-tts",
    )
    # 150/1e6*0.60 + 950/1e6*12.00 = 0.00009 + 0.0114
    assert cost.micro_usd == 11_490
    assert cost.usage_source == "api_reported"
    assert cost.usage_exact is True


def test_unreported_tokens_fall_back_to_declared_assumptions_and_are_labelled():
    cost = compute_cost(
        Usage(characters=600, audio_seconds=38.0),
        price(
            unit="tokens",
            usd_in_per_1m=0.60,
            usd_audio_out_per_1m=12.00,
            est_chars_per_input_token=4.0,
            est_audio_tokens_per_second=25.0,
        ),
        "gpt-4o-mini-tts",
    )
    assert cost.usage_source == "estimated"
    assert cost.usage_exact is False           # the report badges this "est"
    assert "estimated" in cost.basis
    assert "chars=600" in cost.basis and "38.00s" in cost.basis


def test_token_billing_without_declared_assumptions_refuses_to_guess():
    with pytest.raises(Exception, match="est_chars_per_input_token"):
        compute_cost(
            Usage(characters=600, audio_seconds=38.0),
            price(unit="tokens", usd_in_per_1m=0.60),
            "mystery",
        )


def test_costs_are_integer_micro_usd():
    cost = compute_cost(Usage(characters=137), price(unit="per_1k_chars", usd=0.05), "x")
    assert isinstance(cost.micro_usd, int)
    assert fmt_usd(cost.micro_usd) == "$0.0069"


# --------------------------------------------------------------------------
# models.yaml
# --------------------------------------------------------------------------


def test_the_shipped_config_loads_and_declares_two_billing_units():
    reg = load_registry(CONFIGS)
    voice = {m.id: m for m in reg.for_modality("voice", enabled_only=False)}
    assert voice["openai-gpt-4o-mini-tts"].price.unit == "tokens"
    assert voice["openai-tts-1"].price.unit == "per_1m_chars"
    assert voice["elevenlabs-flash-v2-5"].price.unit == "per_1k_chars"


def test_every_price_carries_a_source_and_an_as_of():
    reg = load_registry(CONFIGS)
    for m in reg.for_modality("voice", enabled_only=False):
        assert m.price.source.startswith("http"), m.id
        assert m.price.as_of, m.id


def test_two_models_on_one_vendor_share_a_lane():
    """Quota is per upstream vendor, so the semaphore must be too."""
    reg = load_registry(CONFIGS)
    voice = {m.id: m for m in reg.for_modality("voice", enabled_only=False)}
    assert voice["openai-gpt-4o-mini-tts"].provider == voice["openai-tts-1"].provider == "openai"
    assert voice["elevenlabs-flash-v2-5"].provider == "elevenlabs"


def test_voice_map_pins_one_deliberate_voice_per_provider():
    reg = load_registry(CONFIGS)
    for m in reg.for_modality("voice", enabled_only=False):
        assert "female_mid_warm" in m.voice_map, m.id
        provider_voice, logical = m.resolve_voice("female_mid_warm")
        assert provider_voice and logical == "female_mid_warm"


def test_an_undeclared_voice_is_refused_rather_than_guessed():
    reg = load_registry(CONFIGS)
    m = reg.for_modality("voice", enabled_only=False)[0]
    with pytest.raises(ConfigError, match="voice_map"):
        m.resolve_voice("gravelly_baritone")


def test_a_price_block_missing_its_source_is_rejected(tmp_path):
    (tmp_path / "models.yaml").write_text(
        "version: 1\nvoice:\n  - id: x\n    adapter: openai_tts\n    provider_model: y\n"
        "    auth_env: Z\n    supports: [text_to_speech]\n"
        "    price: {unit: per_1k_chars, usd: 0.05, as_of: 2026-08-31}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="missing `source`"):
        load_registry(tmp_path)


def test_preflight_reports_missing_credentials_without_reading_values(monkeypatch):
    """
    Whatever the enabled arms need, preflight must notice it is absent BEFORE
    any spend. Written against the config rather than a hardcoded variable
    name so flipping which providers are enabled cannot quietly defeat it.
    """
    reg = load_registry(CONFIGS)
    enabled = reg.for_modality("voice")
    assert enabled, "the shipped config has no enabled voice model"
    for env in {m.auth_env for m in enabled} | {reg.asr.auth_env}:
        monkeypatch.delenv(env, raising=False)

    pf = preflight(reg, "voice", [reg.asr])
    assert not pf.ok
    assert {env for _, env in pf.missing_credentials} >= {m.auth_env for m in enabled}
    assert "MISSING" in pf.render()
    # A disabled model is reported as disabled, not as a missing credential -
    # it is not a blocker, it is a documented off switch.
    assert any(mid == "elevenlabs-flash-v2-5" for mid, _ in pf.disabled)


def test_preflight_passes_when_the_enabled_arms_have_their_credentials(monkeypatch):
    reg = load_registry(CONFIGS)
    for m in reg.for_modality("voice"):
        monkeypatch.setenv(m.auth_env, "present")
    monkeypatch.setenv(reg.asr.auth_env, "present")
    assert preflight(reg, "voice", [reg.asr]).ok


# --------------------------------------------------------------------------
# scenarios
# --------------------------------------------------------------------------


def test_yaml_and_csv_produce_the_same_object_shape():
    scenarios = {s.id: s for s in load_scenarios(FIXTURES, "voice")}
    yaml_one, csv_one = scenarios["voi-001"], scenarios["voi-010"]
    assert yaml_one.source_format == "yaml" and csv_one.source_format == "csv"
    for s in (yaml_one, csv_one):
        assert s.modality == "voice" and s.text and s.task and s.max_wer is not None
        assert s.params.get("voice") == "female_mid_warm"


def test_csv_carries_style_and_language_through():
    s = {x.id: x for x in load_scenarios(FIXTURES, "voice")}["voi-011"]
    assert s.task == "styled_tts"
    assert "energetic" in s.style
    assert s.language == "en-US"


def test_a_csv_row_with_no_script_is_an_error_naming_the_row(tmp_path):
    (tmp_path / "s.csv").write_text(
        "id,task,script,expected\nvoi-x,text_to_speech,,something\n", encoding="utf-8"
    )
    with pytest.raises(ScenarioError, match="voi-x.*empty script"):
        load_scenarios(tmp_path, "voice")


def test_a_csv_missing_a_required_column_names_the_column(tmp_path):
    (tmp_path / "s.csv").write_text("id,task,expected\nvoi-x,text_to_speech,y\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match="script"):
        load_scenarios(tmp_path, "voice")


def test_duplicate_scenario_ids_are_fatal(tmp_path):
    for name in ("a.csv", "b.csv"):
        (tmp_path / name).write_text(
            "id,task,script,expected\nvoi-dup,text_to_speech,hello there,ok\n", encoding="utf-8"
        )
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        load_scenarios(tmp_path, "voice")


def test_scenario_weights_that_do_not_sum_to_one_are_rejected(tmp_path):
    (tmp_path / "s.yaml").write_text(
        "id: voi-x\nmodality: voice\ntask: text_to_speech\n"
        "input: {script: hello there}\nexpected: ok\n"
        "weights: {text_accuracy: 0.5, clarity: 0.2}\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError, match="weights sum to 0.700"):
        load_scenarios(tmp_path, "voice")


def test_an_illegal_task_is_rejected_with_the_legal_list(tmp_path):
    (tmp_path / "s.yaml").write_text(
        "id: voi-x\nmodality: voice\ntask: sing_opera\ninput: {script: hi there}\nexpected: ok\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioError, match="not a legal voice task"):
        load_scenarios(tmp_path, "voice")


def test_loading_voice_never_returns_an_image_scenario(tmp_path):
    (tmp_path / "img.yaml").write_text(
        "id: img-001\nmodality: image\ntask: text_to_image\nprompt: a bottle\nexpected: a bottle\n",
        encoding="utf-8",
    )
    (tmp_path / "voi.yaml").write_text(
        "id: voi-001\nmodality: voice\ntask: text_to_speech\ninput: {script: hello}\nexpected: ok\n",
        encoding="utf-8",
    )
    assert [s.id for s in load_scenarios(tmp_path, "voice")] == ["voi-001"]
    assert [s.id for s in load_scenarios(tmp_path, "image")] == ["img-001"]
