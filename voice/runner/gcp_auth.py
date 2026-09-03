"""
One place that decides which Google credential a Vertex client presents.

WHY THIS EXISTS. On 2026-09-03 the Application Default Credential on this
machine lost all authority in the org, mid-bank. Not Vertex authority - ALL
authority: the ADC token could not call `resourcemanager.projects.get` on a
project the same human is Editor of, and could see zero projects. The gcloud
CLI credential for that same account kept working throughout. The two are
different OAuth clients (the CLI's own vs the Google Auth Library), so an org
can allow one and not the other, and one can stop working while the other
does not.

WHAT THIS DOES NOT DO. It does not obtain, store, forge or widen any
credential. `GOOGLE_OAUTH_ACCESS_TOKEN` holds a token the operator minted
themselves with `gcloud auth print-access-token` - the same account, the same
granted roles, the same project, region and models. It changes which OAuth
client asked, nothing about who is asking or what they may do. If the ADC
restriction turns out to be a deliberate control rather than a fault, that is
a question for whoever set it, and this env var should stop being used.

THE COST OF USING IT. A bare access token cannot refresh itself and expires
in about an hour. A run that outlives it fails partway with 401s rather than
degrading, so mint the token in the same command that starts the run. ADC,
when it works, is the better path precisely because it refreshes.
"""

from __future__ import annotations

import os

ACCESS_TOKEN_ENV = "GOOGLE_OAUTH_ACCESS_TOKEN"


def vertex_credentials():
    """
    Explicit credentials when the operator supplied a token, else None.

    Returning None is not a failure - it is the normal path, and tells
    google-auth to resolve Application Default Credentials exactly as before.
    """
    token = (os.environ.get(ACCESS_TOKEN_ENV) or "").strip()
    if not token:
        return None
    from google.oauth2.credentials import Credentials

    return Credentials(token=token)


def gateway_label(default: str = "vertex-adc") -> str:
    """
    What the evidence should say served a call.

    A run records the doorway it used, so a cost or a failure can be traced
    back to a credential path months later. Vertex reached with an operator
    token is still Vertex - same project, same billing, same quota pool - but
    it is not ADC, and the manifest must not claim it was.
    """
    return "vertex-oauth-token" if vertex_credentials() is not None else default
