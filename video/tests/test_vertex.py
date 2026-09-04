"""Vertex auth route: config-selected, same adapters, hard-stop without ADC."""
import pytest

from runner.loaders import JudgeCfg, ModelCfg, Price, load_models
from tests.conftest import REPO_ROOT

PRICE = {"unit": "per_second", "usd": 0.40, "est_usd_per_call": 3.20,
         "source": "t", "as_of": "2026-09-02"}


def _model(**kw):
    base = dict(id="m", enabled=True, adapter="veo_video", provider="google",
                provider_model="x", supports=["text_to_video"], price=PRICE)
    base.update(kw)
    return ModelCfg(**base)


def test_exactly_one_auth_route_required():
    with pytest.raises(Exception, match="exactly one"):
        _model()  # neither auth_env nor vertex
    with pytest.raises(Exception, match="exactly one"):
        _model(auth_env="K", vertex={"project": "p"})  # both
    assert _model(auth_env="K").vertex is None
    assert _model(vertex={"project": "p"}).vertex.location == "global"


def test_judge_auth_route_validated():
    with pytest.raises(Exception, match="exactly one"):
        JudgeCfg(adapter="gemini_text", provider="google", provider_model="x",
                 price=Price(unit="per_token", usd_in_per_1m=1.0,
                             source="t", as_of="2026-09-02"))


def test_shipped_veo_block_uses_vertex_route():
    mf = load_models(REPO_ROOT / "configs" / "models.yaml")
    veo = next(m for m in mf.video if m.id == "veo-3-1-vertex")
    assert veo.vertex.project == "ai-studies-console"
    assert veo.auth_env is None


def test_veo_adapter_builds_vertex_client(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.models = None
            self.operations = None
            self.files = None

    import google.genai as genai_mod
    monkeypatch.setattr(genai_mod, "Client", FakeClient)

    from runner.video.veo_video import build
    build(_model(vertex={"project": "ai-studies-console", "location": "global"}),
          timeout_s=900)
    assert captured["vertexai"] is True
    assert captured["project"] == "ai-studies-console"
    assert "api_key" not in captured

    captured.clear()
    monkeypatch.setenv("K", "fake-key")
    build(_model(auth_env="K"), timeout_s=900)
    assert captured.get("api_key") == "fake-key"
    assert "vertexai" not in captured


def test_vertex_model_without_adc_is_rejected(project, fake_models_yaml,
                                              fake_env, monkeypatch):
    from runner.generate import RunRejected, run_generation
    from runner.loaders import enabled_models, load_scenarios
    import runner.generate as gen

    monkeypatch.setattr(gen, "adc_available", lambda: False)
    text = (project / "configs" / "models-fake.yaml").read_text().replace(
        "auth_env: FAKE_KEY_A", "vertex: {project: ai-studies-console}")
    vertex_yaml = project / "configs" / "models-vertex.yaml"
    vertex_yaml.write_text(text)

    scenarios = load_scenarios(project / "scenarios", modality="video")
    models = enabled_models(load_models(vertex_yaml), "video")
    with pytest.raises(RunRejected, match="application-default"):
        run_generation(project, scenarios, models, "video", budget_usd=20.0)
