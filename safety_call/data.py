"""Synthetic, seeded data for the demo customer. Nothing here is a real person or account."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

DEMO_TODAY = date(2026, 11, 6)  # a Friday: this week's wages have just landed
HISTORY_DAYS = 120
CLOSING_BALANCE = 275.00  # balance at the end of DEMO_TODAY


@dataclass(frozen=True)
class Transaction:
    day: date
    description: str
    amount: float  # positive: money in; negative: money out
    category: str  # "wages", "benefit", "bill", "debt", "spending"


PERSONA = {
    "name": "Amira",
    "full_name": "Amira Saleh",
    "age": 34,
    "city": "Leeds",
    "address": "14 Example Row, Leeds LS0 0AA",
    "adults_in_home": 1,
    "children": [{"age": 7}],
    "work": "Zero-hours care worker",
    "access_needs": "Low vision; uses a screen reader",
    "contact_preference": "Phone, or letters in large print or audio",
    "receives_universal_credit": False,
    "pregnant": False,
    "council_tax": {
        "council": "Leeds City Council",
        "account_ref": "LCC-CT-58213",
        "annual_charge": 1380.00,
        "instalments": 10,
        "discount_applied": False,
    },
    "energy": {"supplier": "BrightSpark Energy", "account_ref": "BSE-4471-0923"},
    "debts": [
        {
            "creditor": "HomeShop Catalogue",
            "description": "HOMESHOP CATALOGUE",
            "balance": 640.00,
            "monthly_payment": 45.00,
            "day_of_month": 18,
            "missed_payments": 2,
        }
    ],
}

# Social-housing rent, charged weekly: (statement text, amount, weekday; 0 = Monday).
WEEKLY_BILLS = [
    ("LEEDS LET HOMES RENT", 90.00, 0),
]

# Direct debits the customer pays every month: (statement text, amount, day of month).
MONTHLY_BILLS = [
    ("AIRE WATER", 32.00, 5),
    ("SIMPLE MOBILE", 15.00, 12),
    ("LEEDS CITY COUNCIL CTAX", 138.00, 24),
    ("BRIGHTSPARK ENERGY", 118.00, 24),
]

LABELS = {
    "LEEDS LET HOMES RENT": "Rent",
    "AIRE WATER": "Water",
    "SIMPLE MOBILE": "Mobile phone",
    "LEEDS CITY COUNCIL CTAX": "Council Tax",
    "BRIGHTSPARK ENERGY": "Energy (BrightSpark)",
    "HOMESHOP CATALOGUE": "HomeShop Catalogue",
    "CAREWELL AGENCY PAY": "Wages (Carewell Agency)",
    "HMRC CHILD BENEFIT": "Child Benefit",
}

WAGES_DESCRIPTION = "CAREWELL AGENCY PAY"
USUAL_WAGE = 320.0
CUT_WAGE = 190.0
HOURS_CUT_FROM = date(2026, 10, 23)

CHILD_BENEFIT = 104.20
CHILD_BENEFIT_ANCHOR = date(2026, 11, 16)  # a Monday; paid every 4 weeks

# Direct debits the bank saw fail (the catalogue payments in September and October).
FAILED_PAYMENTS = [
    {"day": date(2026, 9, 18), "payee": "HomeShop Catalogue", "amount": 45.00},
    {"day": date(2026, 10, 18), "payee": "HomeShop Catalogue", "amount": 45.00},
]


def generate_transactions(seed: int = 11) -> list[Transaction]:
    """Four months of statement lines ending on DEMO_TODAY, identical on every run."""
    rng = random.Random(seed)
    start = DEMO_TODAY - timedelta(days=HISTORY_DAYS - 1)
    txs: list[Transaction] = []
    day = start
    while day <= DEMO_TODAY:
        for description, amount, dom in MONTHLY_BILLS:
            if day.day == dom:
                txs.append(Transaction(day, description, -amount, "bill"))
        for description, amount, weekday in WEEKLY_BILLS:
            if day.weekday() == weekday:
                txs.append(Transaction(day, description, -amount, "bill"))
        if day.weekday() == 4:  # Friday
            cut = day >= HOURS_CUT_FROM
            mean, sd = (CUT_WAGE, 10.0) if cut else (USUAL_WAGE, 22.0)
            pay = round(max(0.0, rng.gauss(mean, sd)), 2)
            txs.append(Transaction(day, WAGES_DESCRIPTION, pay, "wages"))
        if (CHILD_BENEFIT_ANCHOR - day).days % 28 == 0:
            txs.append(Transaction(day, "HMRC CHILD BENEFIT", CHILD_BENEFIT, "benefit"))
        if day.weekday() in (0, 3):  # Monday and Thursday shop
            txs.append(Transaction(day, "SUPERMARKET", -round(rng.uniform(22, 34), 2), "spending"))
        if rng.random() < 0.5:
            txs.append(Transaction(day, "CORNER SHOP", -round(rng.uniform(2.5, 7.5), 2), "spending"))
        if day.weekday() < 5 and rng.random() < 0.35:
            txs.append(Transaction(day, "WEST YORKS BUS", -5.50, "spending"))
        if rng.random() < 0.03:
            txs.append(Transaction(day, "PHARMACY", -round(rng.uniform(6, 14), 2), "spending"))
        day += timedelta(days=1)

    # The catalogue was paid in July and August; September and October failed.
    for month in (7, 8):
        paid = date(2026, month, 18)
        if paid >= start:
            txs.append(Transaction(paid, "HOMESHOP CATALOGUE", -45.00, "debt"))

    txs.sort(key=lambda t: (t.day, t.amount < 0, t.description))
    return txs


def opening_balance(txs: list[Transaction]) -> float:
    return round(CLOSING_BALANCE - sum(t.amount for t in txs), 2)


def label(description: str) -> str:
    return LABELS.get(description, description.title())
