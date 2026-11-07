"""Prediction firewall: the customer's forecast is theirs.

"You may struggle soon" is sensitive. If it could lower a credit limit or a credit
score, customers would rightly avoid the service. So every forecast, life event and
support check is stored with one allowed purpose, and any other use is refused in
code (and logged), not just promised in a policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ALLOWED_PURPOSES = frozenset({"customer_support", "customer_view"})

# Uses a bank might be tempted by, refused by design.
BLOCKED_PURPOSES = {
    "credit_decision": "lending decisions",
    "credit_limit": "credit limit changes",
    "credit_scoring": "credit scoring or reporting",
    "pricing": "pricing or interest rates",
    "marketing": "marketing",
    "collections_escalation": "collections escalation",
}

POLICY = (
    "Forecasts, life events and support checks from Safety Call are used only to help "
    "the customer. They are never used for lending decisions, credit limits, credit "
    "scoring, pricing, marketing or collections escalation."
)


class PurposeRefused(PermissionError):
    pass


@dataclass
class FirewalledStore:
    """Holds support-only insights and checks the purpose of every read."""

    _items: dict = field(default_factory=dict)
    audit: list = field(default_factory=list)

    def put(self, key: str, value) -> None:
        self._items[key] = value

    def get(self, key: str, purpose: str):
        allowed = purpose in ALLOWED_PURPOSES
        self.audit.append(
            {
                "at": datetime.now().strftime("%H:%M:%S"),
                "key": key,
                "purpose": purpose,
                "allowed": allowed,
            }
        )
        if not allowed:
            label = BLOCKED_PURPOSES.get(purpose, purpose)
            raise PurposeRefused(f"Safety Call insights cannot be used for {label}.")
        return self._items[key]
