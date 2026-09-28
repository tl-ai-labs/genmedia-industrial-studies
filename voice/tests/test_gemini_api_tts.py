"""
Gemini 3.8 TTS over the Gemini API - offline. requests.post and the Secret
Manager read are replaced, so no call leaves the machine and no credential is
needed.

What these pin down is what the 2026-09-28 live probe established: the style
travels in speech_metadata and never in the text (3.8 would speak it), the
voice goes in prebuiltVoiceConfig, the audio comes back as exactly one WAV at
the rate the mimeType states, the key comes from Secret Manager and never
from the environment, and no error message carries the key.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import struct
from pathlib import Path

import pytest
import requests

from runner.adapters import AuthError, GenRequest, ProviderError, RateLimited, SafetyRefusal
from runner.adapters import gemini_api_tts as mod
from runner.adapters.gemini_api_tts import GeminiApiTtsAdapter
from runner.models import load_registry

CONFIGS = Path(__file__).resolve().parent.parent / "configs"
PCM = b"\x01\x00\x02\x00" * 100
SCRIPT = "Your order 4417 ships on Monday."
SECRET = "projects/temp-genmedia-study/secrets/gemini-tts-key/versions/latest"
KEY = "AQ.test-key-value-0123456789"
L16 = "audio/l16; rate=24000; channels=1"


def _spec():
    reg = load_registry(CONFIGS)
    return next(m for m in reg.models if m.id == "gemini-3-8-flash-tts")


def _req(style=None, ttfa=False):
    return GenRequest(task="styled_tts", text=SCRIPT, params={"voice": "female_mid_warm"},
                      voice_id="Aoede", voice_logical="female_mid_warm", style=style,
                      measure_ttfa=ttfa)


def _chunk(pcm=PCM, mime=L16, usage=None, finish=None, audio=True):
    parts = [{"inlineData": {"mimeType": mime, "data": base64.b64encode(pcm).decode()}}] if audio else []
    cand = {"content": {"parts": parts}}
    if finish:
        cand["finishReason"] = finish
    out = {"candidates": [cand], "modelVersion": "gemini-3.8-flash-tts", "responseId": "r-1"}
    if usage:
        out["usageMetadata"] = usage
    return out


class _Resp:
    def __init__(self, status=200, body=None, lines=(), headers=None):
        self.status_code = status
        self._body = body or {}
        self._lines = lines
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def calls(monkeypatch):
    """Records every request and replays the queued responses in order."""
    state = {"sent": [], "queue": []}

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        state["sent"].append({"url": url, "headers": headers, "json": json, "stream": stream})
        return state["queue"].pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("GEMINI_API_KEY_SECRET", SECRET)
    monkeypatch.setattr(mod, "_fetch_api_key", lambda secret: KEY)
    return state


# ------------------------------------------------------------------ request


def test_style_goes_in_speech_metadata_and_the_text_is_sent_verbatim():
    adapter = GeminiApiTtsAdapter.__new__(GeminiApiTtsAdapter)
    adapter.supports = ("text_to_speech", "styled_tts")
    body, applied = adapter.build_body(_req(style="calm, reassuring"))
    part = body["contents"][0]["parts"][0]
    assert part["text"] == SCRIPT  # no "Say the following..." preamble
    assert part["speech_metadata"] == {"style": "calm, reassuring"}
    assert applied["style"] == "calm, reassuring"
    cfg = body["generationConfig"]
    assert cfg["speechConfig"]["voiceConfig"] == {"prebuiltVoiceConfig": {"voiceName": "Aoede"}}
    assert cfg["responseFormat"]["audio"] == {"mimeType": "AUDIO_L16", "sampleRate": 24000}


def test_no_style_means_no_speech_metadata_and_no_style_claimed():
    adapter = GeminiApiTtsAdapter.__new__(GeminiApiTtsAdapter)
    adapter.supports = ("text_to_speech", "styled_tts")
    body, applied = adapter.build_body(_req())
    assert "speech_metadata" not in body["contents"][0]["parts"][0]
    assert "style" not in applied


# -------------------------------------------------------------- unary call


def test_unary_call_returns_one_wav_and_the_reported_usage(calls):
    usage = {"promptTokenCount": 12, "candidatesTokenCount": 75, "totalTokenCount": 87}
    calls["queue"].append(_Resp(body=_chunk(usage=usage)))
    res = GeminiApiTtsAdapter(_spec()).run(_req(style="warm"))

    sent = calls["sent"][0]
    assert sent["url"].endswith("/models/gemini-3.8-flash-tts:generateContent")
    assert sent["headers"]["x-goog-api-key"] == KEY
    assert "Authorization" not in sent["headers"]
    assert res.data[:4] == b"RIFF" and res.data.count(b"RIFF") == 1
    assert len(res.data) == 44 + len(PCM)
    assert res.usage.reported and res.usage.input_tokens == 12 and res.usage.audio_out_tokens == 75
    assert res.provider_request_id == "r-1" and res.ttfa_ms is None
    assert res.provider_version == "gemini-3.8-flash-tts"


def test_the_wav_header_carries_the_rate_the_mimetype_states(calls):
    calls["queue"].append(_Resp(body=_chunk(mime="audio/L16;codec=pcm;rate=16000")))
    res = GeminiApiTtsAdapter(_spec()).run(_req())
    assert struct.unpack("<I", res.data[24:28])[0] == 16000
    assert res.applied_params["sample_rate"] == 16000


def test_a_wav_from_the_service_is_not_wrapped_a_second_time(calls):
    # The probe saw exactly this when responseFormat was left out.
    wav = mod._wrap_pcm_as_wav(PCM, 24000)
    calls["queue"].append(_Resp(body=_chunk(pcm=wav, mime="audio/wav")))
    res = GeminiApiTtsAdapter(_spec()).run(_req())
    assert res.data == wav


def test_non_pcm_audio_is_refused_rather_than_mislabelled(calls):
    calls["queue"].append(_Resp(body=_chunk(pcm=b"ID3xxxx", mime="audio/mpeg")))
    with pytest.raises(ProviderError, match="not 16-bit PCM"):
        GeminiApiTtsAdapter(_spec()).run(_req())


def test_no_inline_data_is_a_clear_error_naming_the_finish_reason(calls):
    calls["queue"].append(_Resp(body=_chunk(audio=False, finish="OTHER")))
    with pytest.raises(ProviderError, match="no inlineData.*finishReason=OTHER"):
        GeminiApiTtsAdapter(_spec()).run(_req())


def test_a_clip_cut_at_the_token_limit_is_rejected_and_not_retried(calls):
    calls["queue"].append(_Resp(body=_chunk(finish="MAX_TOKENS")))
    with pytest.raises(ProviderError, match="output-token limit") as info:
        GeminiApiTtsAdapter(_spec()).run(_req())
    assert info.value.retryable is False


# ------------------------------------------------------------- streaming


def test_streamed_call_concatenates_chunks_and_times_first_audio(calls):
    first = _chunk(pcm=PCM[:200])
    last = _chunk(pcm=PCM[200:], usage={"promptTokenCount": 9, "candidatesTokenCount": 50})
    lines = ["", f"data: {json.dumps(first)}", "", f"data: {json.dumps(last)}"]
    calls["queue"].append(_Resp(lines=lines))
    res = GeminiApiTtsAdapter(_spec()).run(_req(ttfa=True))

    sent = calls["sent"][0]
    assert sent["url"].endswith(":streamGenerateContent?alt=sse") and sent["stream"]
    assert res.data[44:] == PCM and res.data.count(b"RIFF") == 1
    assert res.ttfa_ms is not None and res.ttfa_ms >= 0
    assert res.usage.audio_out_tokens == 50


# ----------------------------------------------------------------- errors


@pytest.mark.parametrize("status,exc,retryable", [
    (429, RateLimited, True), (500, ProviderError, True), (503, ProviderError, True),
    (403, AuthError, False),
])
def test_http_failures_map_onto_the_runner_taxonomy(calls, status, exc, retryable):
    calls["queue"].append(_Resp(status=status, body={"error": {"status": "X", "message": "nope"}}))
    with pytest.raises(exc) as info:
        GeminiApiTtsAdapter(_spec()).run(_req())
    assert info.value.retryable is retryable


def test_retry_after_reaches_the_runner(calls):
    calls["queue"].append(_Resp(status=429, body={"error": {}}, headers={"Retry-After": "7"}))
    with pytest.raises(RateLimited) as info:
        GeminiApiTtsAdapter(_spec()).run(_req())
    assert info.value.retry_after == 7.0


def test_a_safety_stop_is_a_refusal_not_a_retryable_error(calls):
    calls["queue"].append(_Resp(body=_chunk(finish="SAFETY")))
    with pytest.raises(SafetyRefusal):
        GeminiApiTtsAdapter(_spec()).run(_req())


def test_no_error_message_carries_the_key(calls):
    # A provider echoing the key back must not put it in telemetry.
    calls["queue"].append(_Resp(status=400, body={"error": {"message": f"bad key {KEY}"}}))
    with pytest.raises(ProviderError) as info:
        GeminiApiTtsAdapter(_spec()).run(_req())
    assert KEY not in str(info.value) and "<redacted>" in str(info.value)


def test_a_network_failure_does_not_echo_the_request(calls, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError(f"failed sending headers x-goog-api-key={KEY}")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(ProviderError) as info:
        GeminiApiTtsAdapter(_spec()).run(_req())
    assert KEY not in str(info.value)


# ------------------------------------------------------------------ secret


def test_missing_secret_name_is_an_auth_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY_SECRET", raising=False)
    with pytest.raises(AuthError, match="GEMINI_API_KEY_SECRET"):
        GeminiApiTtsAdapter(_spec())


def test_a_raw_key_in_the_env_var_is_refused_and_not_echoed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_SECRET", KEY)
    with pytest.raises(AuthError) as info:
        GeminiApiTtsAdapter(_spec())
    assert KEY not in str(info.value)


class _Session:
    def __init__(self, resp):
        self.resp, self.urls = resp, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.resp


def _secret_manager(monkeypatch, resp):
    import google.auth
    import google.auth.transport.requests as tr

    session = _Session(resp)
    monkeypatch.delenv("GOOGLE_OAUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (object(), "p"))
    monkeypatch.setattr(tr, "AuthorizedSession", lambda creds: session)
    monkeypatch.setattr(mod, "_key_cache", {})
    return session


def test_the_key_is_read_from_secret_manager_once_per_process(monkeypatch):
    payload = {"payload": {"data": base64.b64encode(f"{KEY}\n".encode()).decode()}}
    session = _secret_manager(monkeypatch, _Resp(body=payload))
    assert mod._fetch_api_key(SECRET) == KEY
    assert mod._fetch_api_key(SECRET) == KEY
    assert session.urls == [f"https://secretmanager.googleapis.com/v1/{SECRET}:access"]


def test_a_secret_manager_refusal_is_an_auth_error(monkeypatch):
    _secret_manager(monkeypatch, _Resp(status=403, body={"error": {"status": "PERMISSION_DENIED"}}))
    with pytest.raises(AuthError, match="Secret Manager refused.*403 PERMISSION_DENIED"):
        mod._fetch_api_key(SECRET)


# ------------------------------------------------------------------ label


def test_the_board_says_gemini_api_not_vertex():
    assert _spec().served_from == "Gemini API · no region choice"
    assert _spec().auth_env == "GEMINI_API_KEY_SECRET"
