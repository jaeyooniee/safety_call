"""Life-event detection: read what happened from transactions, before the customer asks.

Classifying by *event* (job loss, illness, a new baby) rather than by the question a
customer would ask means the right support bundle is ready when the call starts.

Every rule is a plain, explainable threshold. An event is a signal, not a fact: the
agent asks the customer whether it is right, and the customer can correct it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import data
from .household import Household

PHARMACY_WORDS = ("PHARMACY", "CHEMIST", "PRESCRIPTION", "GP SURGERY", "DENTAL")
BABY_WORDS = ("BABY", "NAPPIES", "NAPPY", "INFANT FORMULA", "PRAM", "MATERNITY")


@dataclass(frozen=True)
class LifeEvent:
    key: str  # "reduced_hours", "job_loss", "illness", "new_baby", "payment_difficulty"
    name: str
    confidence: str  # "high" or "medium"
    evidence: tuple[str, ...]
    since: date | None = None
    bundle: tuple[str, ...] = field(default_factory=tuple)  # support item ids

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "since": self.since.isoformat() if self.since else None,
            "bundle": list(self.bundle),
        }


# Which support to prepare for each event (ids from support.CATALOGUE).
BUNDLES = {
    "reduced_hours": (
        "universal_credit",
        "council_tax_single_person_discount",
        "energy_payment_plan",
    ),
    "job_loss": ("universal_credit", "new_style_jsa", "council_tax_reduction", "energy_payment_plan"),
    "illness": ("statutory_sick_pay", "pip", "prescription_prepayment"),
    "new_baby": ("child_benefit_claim", "healthy_start", "sure_start_maternity_grant"),
    "payment_difficulty": ("breathing_space", "debt_advice_handover"),
}


def _spend(txs, words, start: date, end: date) -> float:
    return sum(
        -t.amount
        for t in txs
        if t.amount < 0 and start < t.day <= end and any(w in t.description for w in words)
    )


def detect(
    txs: list[data.Transaction],
    household: Household,
    failed_payments: list[dict],
    today: date,
) -> list[LifeEvent]:
    events: list[LifeEvent] = []
    w = household.wages

    # Job loss: wages were regular, then nothing for two or more paydays.
    pay_days = sorted(t.day for t in txs if t.category == "wages")
    if pay_days and (today - pay_days[-1]).days >= 15:
        events.append(
            LifeEvent(
                "job_loss",
                "Wages have stopped",
                "high",
                (
                    f"Regular weekly wages until {pay_days[-1]:%d %B}",
                    f"No wages for {(today - pay_days[-1]).days} days",
                ),
                since=pay_days[-1],
                bundle=BUNDLES["job_loss"],
            )
        )
    # Reduced hours: wages still arrive, but well below the usual amount.
    elif w.hours_cut:
        drop = 1 - w.recent_weekly / w.usual_weekly
        events.append(
            LifeEvent(
                "reduced_hours",
                "Working hours cut",
                "high" if w.recent_weeks >= 3 else "medium",
                (
                    f"Last {w.recent_weeks} weekly wages average £{w.recent_weekly:.2f}",
                    f"Usual weekly wage £{w.usual_weekly:.2f} ({drop:.0%} lower)",
                ),
                since=w.cut_since,
                bundle=BUNDLES["reduced_hours"],
            )
        )

    # Illness: health spending in the last 30 days far above the usual monthly level.
    recent = _spend(txs, PHARMACY_WORDS, today - timedelta(days=30), today)
    earlier = _spend(txs, PHARMACY_WORDS, today - timedelta(days=120), today - timedelta(days=30)) / 3
    if recent >= 30 and recent >= 3 * max(earlier, 5.0):
        events.append(
            LifeEvent(
                "illness",
                "Possible illness",
                "medium",
                (
                    f"£{recent:.2f} on pharmacy and health costs in the last 30 days",
                    f"Usually about £{earlier:.2f} a month",
                ),
                bundle=BUNDLES["illness"],
            )
        )

    # New baby: baby-goods spending appears where there was almost none.
    recent = _spend(txs, BABY_WORDS, today - timedelta(days=60), today)
    earlier = _spend(txs, BABY_WORDS, today - timedelta(days=120), today - timedelta(days=60))
    if recent >= 60 and earlier < 15:
        events.append(
            LifeEvent(
                "new_baby",
                "Possible new baby",
                "medium",
                (f"£{recent:.2f} on baby goods in the last 60 days", "Almost none before that"),
                bundle=BUNDLES["new_baby"],
            )
        )

    # Payment difficulty: Direct Debits that failed recently.
    failed = [f for f in failed_payments if (today - f["day"]).days <= 90]
    if len(failed) >= 2:
        payees = sorted({f["payee"] for f in failed})
        events.append(
            LifeEvent(
                "payment_difficulty",
                "Missed payments",
                "high",
                (f"{len(failed)} failed Direct Debits in 90 days ({', '.join(payees)})",),
                since=min(f["day"] for f in failed),
                bundle=BUNDLES["payment_difficulty"],
            )
        )
    return events
