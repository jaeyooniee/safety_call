"""Facts with sources, and tracing the figures the agent speaks back to them.

Every tool result carries facts. When the agent speaks, each money amount,
percentage and date in the sentence is matched to a fact, so the "Why?" panel can
show where it came from, and any figure without a source is flagged.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}

MONEY = re.compile(r"£\s?(\d[\d,]*(?:\.\d{1,2})?)")
PERCENT = re.compile(r"\b(\d{1,3})\s?(?:%|per ?cent\b)", re.IGNORECASE)
DATE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def money(label: str, amount: float, source: str) -> dict:
    return {"label": label, "value": f"£{amount:,.2f}", "source": source, "keys": [f"money:{amount:.2f}"]}


def percent(label: str, probability: float, source: str) -> dict:
    p = round(probability * 100)
    return {"label": label, "value": f"{p}%", "source": source, "keys": [f"pct:{p}"]}


def day(label: str, d: date, source: str) -> dict:
    return {"label": label, "value": f"{d.day} {d:%B %Y}", "source": source, "keys": [f"date:{d.month}-{d.day}"]}


def text(label: str, value: str, source: str) -> dict:
    return {"label": label, "value": value, "source": source, "keys": []}


def _mentions(utterance: str) -> list[tuple[str, str]]:
    found = []
    for m in MONEY.finditer(utterance):
        found.append((m.group(0), "money:" + m.group(1).replace(",", "")))
    for m in PERCENT.finditer(utterance):
        found.append((m.group(0), f"pct:{int(m.group(1))}"))
    for m in DATE.finditer(utterance):
        found.append((m.group(0), f"date:{MONTHS[m.group(2).lower()]}-{int(m.group(1))}"))
    return found


def _matches(key: str, fact_key: str) -> bool:
    kind, _, value = key.partition(":")
    fkind, _, fvalue = fact_key.partition(":")
    if kind != fkind:
        return False
    if kind == "money":
        a, b = float(value), float(fvalue)
        # Spoken amounts are often rounded: "£118" for £118.00, "about £35" for £34.50.
        return abs(a - b) <= 1.0 or round(b) == a or round(b, -1) == a
    if kind == "pct":
        return abs(int(value) - int(fvalue)) <= 1
    return value == fvalue


def trace(utterance: str, facts: list[dict]) -> list[dict]:
    """Each figure in the utterance, with the fact it came from (or None)."""
    out = []
    for shown, key in _mentions(utterance):
        source = next(
            (f for f in reversed(facts) for k in f["keys"] if _matches(key, k)),
            None,
        )
        out.append({"figure": shown, "fact": source})
    return out
