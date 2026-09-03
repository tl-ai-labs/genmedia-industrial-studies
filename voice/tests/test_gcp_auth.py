"""
The credential resolver, and the promise it makes to the evidence.

Offline: nothing here contacts Google. What is worth pinning is that the
default path is untouched, and that a run reached with an operator token
cannot claim in its manifest to have used ADC.
"""

from __future__ import annotations

import pytest

from runner.gcp_auth import ACCESS_TOKEN_ENV, gateway_label, vertex_credentials


def test_no_token_means_adc_exactly_as_before(monkeypatch):
    monkeypatch.delenv(ACCESS_TOKEN_ENV, raising=False)
    # None is the signal to google-auth to resolve ADC itself. It must not be
    # an object, or the default path silently changes for every existing user.
    assert vertex_credentials() is None
    assert gateway_label() == "vertex-adc"


def test_a_blank_token_is_treated_as_absent(monkeypatch):
    """An empty env var is a config slip, not an instruction to send no auth."""
    for blank in ("", "   ", "\n"):
        monkeypatch.setenv(ACCESS_TOKEN_ENV, blank)
        assert vertex_credentials() is None
        assert gateway_label() == "vertex-adc"


def test_a_token_produces_credentials_carrying_it(monkeypatch):
    monkeypatch.setenv(ACCESS_TOKEN_ENV, "ya29.test-token")
    creds = vertex_credentials()
    assert creds is not None and creds.token == "ya29.test-token"


def test_the_gateway_label_never_claims_adc_when_a_token_was_used(monkeypatch):
    """
    The evidence rule. Vertex reached with an operator token is still Vertex -
    same project, same billing, same quota pool - but it is NOT ADC, and a
    manifest that says otherwise makes a credential path untraceable later.
    """
    monkeypatch.setenv(ACCESS_TOKEN_ENV, "ya29.test-token")
    assert gateway_label() == "vertex-oauth-token"
    assert gateway_label(default="anything-else") == "vertex-oauth-token"
