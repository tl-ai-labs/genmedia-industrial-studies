"""The voice import from the committed dashboard export."""
import json

from conftest import ELEV, GTTS, mp3_with_id3

from runner.export import export
from runner.voice_dashboard import RUN_ID, load_dashboard

CARD = """
<article class="sc" data-ind="{ind}"
  data-res="tie" data-gem="" data-oth="" data-gap="">
  <div class="schead"><div><span class="eyebrow">{ind}</span>
  <h3><code>{sid}</code>{title}</h3></div></div>
  <details><summary>The script sent to both models</summary>
  {script}</details>
  <div class="vs">{cols}</div>
</article>
"""
CLIP = """
<div class="clip"><div class="head"><span class="rl">{label}</span>
  <span class="sc-pill">{pill}</span></div>
  <audio controls preload="none" src="audio/{fname}"></audio></div>
"""


def _dashboard(tmp_path):
    d = tmp_path / "dashboard"
    (d / "audio").mkdir(parents=True)

    def clip(sid, model, variant, label, pill):
        fname = f"{sid}--{model}--" + (f"{variant}--" if variant else "") + f"{label}.mp3"
        (d / "audio" / fname).write_bytes(mp3_with_id3(model))
        return CLIP.format(label=label, pill=pill, fname=fname)

    # vr-ads-05: two passes, both models scored on p2 only -> p2 is picked
    ads = "".join([clip("vr-ads-05", GTTS, "", "voice-p1", "0.0%"),
                   clip("vr-ads-05", GTTS, "", "voice-p2", "94.5%"),
                   clip("vr-ads-05", ELEV, "", "voice-p1", "72.3%"),
                   clip("vr-ads-05", ELEV, "", "voice-p2", "95.0%")])
    # vr-ecom-06: variants bare/nato -> one item per scenario = first scored pair (bare)
    ecom = "".join([clip("vr-ecom-06", GTTS, "bare", "voice-f1", "98.1%"),
                    clip("vr-ecom-06", GTTS, "nato", "voice-f1", "97.0%"),
                    clip("vr-ecom-06", ELEV, "bare", "voice-f1", "88.0%"),
                    clip("vr-ecom-06", ELEV, "nato", "voice-f1", "89.0%")])
    # vr-game-02: only one model has clips -> skipped
    game = clip("vr-game-02", GTTS, "", "voice-p1", "0.0%")
    html = "<html><body><div id='cards'>" + "".join([
        CARD.format(ind="Ads", sid="vr-ads-05", title="Brand name pronunciation - Ads",
                    script='<p class="script">Say Nykaa &amp; Bhiwandi correctly.</p>', cols=ads),
        CARD.format(ind="Ecommerce &amp; Retail", sid="vr-ecom-06", title="KYC readback",
                    script='<div class="vlab"><code>bare</code></div><p class="script">Your reference is A B C.</p>'
                           '<div class="vlab"><code>nato</code></div><p class="script">Alpha Bravo Charlie.</p>', cols=ecom),
        CARD.format(ind="Gaming", sid="vr-game-02", title="Barks", script='<p class="script">Go go go!</p>', cols=game),
    ]) + "</div></body></html>"
    (d / "index.html").write_text(html)
    return d


def test_one_item_per_scenario_prefers_a_scored_pair(tmp_path):
    d = _dashboard(tmp_path)
    data = load_dashboard(d, tmp_path / "scores")
    assert data.lane == "voice" and data.run_id == RUN_ID
    assert [p.scenario_id for p in data.pairs] == ["vr-ads-05", "vr-ecom-06#bare"]
    ads = data.pairs[0]
    assert {a.model_id for a in ads.arms} == {GTTS, ELEV}
    assert all(a.media.name.endswith("voice-p2.mp3") for a in ads.arms)
    assert {a.model_id: a.score for a in ads.arms} == {GTTS: 0.945, ELEV: 0.95}
    assert ads.industry == "Ads" and ads.brief == "Say Nykaa & Bhiwandi correctly."
    assert ads.title == "Brand name pronunciation - Ads" and ads.kind == "audio"
    assert data.pairs[1].industry == "Ecommerce & Retail"
    assert data.pairs[1].brief == "Your reference is A B C."      # only the bare variant's line
    assert data.skipped == [("vr-game-02", "no (variant, pass) with a clip from both models")]
    # the synthetic scores.jsonl is what the correlation will read
    rows = [json.loads(l) for l in (tmp_path / "scores" / "scores.jsonl").read_text().splitlines()]
    assert {(r["scenario_id"], r["model_id"]): r["score"] for r in rows}[("vr-ads-05", GTTS)] == 0.945
    assert (tmp_path / "scores" / "manifest.json").exists()


def test_all_items_emits_every_matched_pair(tmp_path):
    d = _dashboard(tmp_path)
    data = load_dashboard(d, tmp_path / "scores", items="all")
    ids = [p.scenario_id for p in data.pairs]
    assert ids == ["vr-ads-05@voice-p1", "vr-ads-05@voice-p2",
                   "vr-ecom-06#bare@voice-f1", "vr-ecom-06#nato@voice-f1"]
    p1 = data.pairs[0]
    assert {a.model_id: a.score for a in p1.arms} == {GTTS: None, ELEV: 0.723}
    assert data.pairs[3].brief == "Alpha Bravo Charlie."


def test_dashboard_items_export_blind(tmp_path):
    d = _dashboard(tmp_path)
    data = load_dashboard(d, tmp_path / "private" / "voice-dashboard")
    s = export([], dist=tmp_path / "dist", private=tmp_path / "private", salt="x", extra=[data])
    assert s["n_items"] == 2 and s["lanes"]["voice"]["runs"] == [RUN_ID]
    for p in (tmp_path / "dist").rglob("*"):
        if p.is_file():
            blob = p.read_bytes()
            assert b"elevenlabs" not in blob and b"gemini" not in blob, p
            assert "elevenlabs" not in p.name and "gemini" not in p.name
    key = json.loads((tmp_path / "private" / "key.json").read_text())
    assert key["runs"]["voice"][RUN_ID].endswith("voice-dashboard")
    items = json.loads((tmp_path / "dist" / "items.json").read_text())["items"]
    assert {i["industry"] for i in items} == {"Ads", "Ecommerce & Retail"}
