"""Number formats — the only place a figure's printed form is decided.

Every template prints numbers through these filters, never with an inline
`'%.2f'|format`, so internal and client reports, and all three lanes, show the
same quantity the same way.

Audience rule: internal reports print quality on the rubric's 0-10 scale;
client reports print it as a percentage of that scale, and quality GAPS as
percentage points (pp).
"""
from __future__ import annotations

from jinja2 import Undefined


def _missing(v) -> bool:
    """None, or a key the context does not carry: both print as a dash."""
    return v is None or isinstance(v, Undefined)


def _strip(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def make_filters(names: dict | None = None, client: bool = False) -> dict:
    nm = names or {}

    def usd(micro) -> str:
        """Micro-dollars as dollars, four decimals: generation costs per
        output are fractions of a cent apart, and two decimals would tie them."""
        if _missing(micro):
            return "—"
        return f"${micro / 1e6:,.4f}"

    def secs(ms) -> str:
        """Milliseconds as seconds, one decimal."""
        return "—" if _missing(ms) else f"{ms / 1000:.1f}s"

    def millis(ms) -> str:
        """Milliseconds kept as milliseconds (time to first audio and similar)."""
        return "—" if _missing(ms) else f"{ms:.0f} ms"

    def q(v, short: bool = False) -> str:
        """A quality score in the audience's units. Full form keeps one decimal
        (client) or two (internal). Short form — per-scenario badges — drops
        only trailing zeros: any difference decides a scenario, so two
        different scores must never print alike."""
        if _missing(v):
            return "—"
        if client:
            return (_strip(f"{v * 10:.2f}") + "%") if short else f"{v * 10:.1f}%"
        return _strip(f"{v:.3f}") if short else f"{v:.2f}"

    def qd(v, short: bool = False) -> str:
        """A quality GAP: points internally, percentage points for the client."""
        if _missing(v):
            return "—"
        if short:
            return (_strip(f"{v * 10:+.2f}") + " pp") if client else _strip(f"{v:+.3f}")
        return f"{v * 10:+.1f} pp" if client else f"{v:+.2f}"

    def pct(ratio, decimals: int = 0) -> str:
        """A 0-1 ratio as a percentage (gate pass rate, success, win share)."""
        if _missing(ratio):
            return "—"
        return f"{ratio * 100:.{decimals}f}%"

    def win_pct(value) -> str:
        """A 0-100 win percentage with one decimal, trailing zero dropped:
        5 of 8 and 3 of 8 read 62.5% and 37.5%, never two numbers rounded in
        opposite directions; 4 of 8 reads 50%."""
        return "—" if _missing(value) else _strip(f"{value:.1f}") + "%"

    return {
        "usd": usd, "s": secs, "ms": millis, "q": q, "qd": qd, "pct": pct,
        "win_pct": win_pct,
        "disp": lambda mid: nm.get(mid, mid),
        # "text_to_video" -> "Text to video": a task key is an identifier,
        # not a heading a reader should have to decode
        "tasktitle": lambda t: str(t).replace("_", " ").capitalize() if t else "",
    }
