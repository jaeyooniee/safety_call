"""Household model: what the bank can infer from a customer's transactions.

Plain, deterministic rules: recurring bills, the wage pattern (and whether hours
were cut), four-weekly benefits and everyday spending.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median, pstdev

from . import data


@dataclass(frozen=True)
class RecurringBill:
    key: str  # stable id, e.g. "council_tax"
    name: str
    amount: float
    kind: str  # "bill" or "debt"
    source: str
    frequency: str = "monthly"  # or "weekly"
    day_of_month: int = 1  # monthly bills
    last_paid: date | None = None  # weekly bills

    def due_dates(self, start: date, end: date) -> list[date]:
        """Due dates in (start, end]."""
        out = []
        if self.frequency == "weekly":
            due = self.last_paid + timedelta(days=7)
            while due <= end:
                if due > start:
                    out.append(due)
                due += timedelta(days=7)
            return out
        year, month = start.year, start.month
        while True:
            last = calendar.monthrange(year, month)[1]
            due = date(year, month, min(self.day_of_month, last))
            if due > end:
                return out
            if due > start:
                out.append(due)
            month += 1
            if month == 13:
                year, month = year + 1, 1


@dataclass(frozen=True)
class WagePattern:
    weekday: int
    usual_weekly: float
    usual_sd: float
    recent_weekly: float
    recent_sd: float
    recent_weeks: int
    hours_cut: bool
    cut_since: date | None


@dataclass(frozen=True)
class FourWeeklyIncome:
    name: str
    amount: float
    last_paid: date


@dataclass(frozen=True)
class Household:
    today: date
    balance: float
    bills: tuple[RecurringBill, ...]
    wages: WagePattern
    benefits: tuple[FourWeeklyIncome, ...]
    daily_spend_mean: float
    daily_spend_sd: float

    def bill(self, key: str) -> RecurringBill | None:
        return next((b for b in self.bills if b.key == key), None)


BILL_KEYS = {
    "LEEDS LET HOMES RENT": "rent",
    "AIRE WATER": "water",
    "SIMPLE MOBILE": "phone",
    "LEEDS CITY COUNCIL CTAX": "council_tax",
    "BRIGHTSPARK ENERGY": "energy",
}


def _recurring_bills(txs: list[data.Transaction]) -> list[RecurringBill]:
    groups: dict[str, list[data.Transaction]] = defaultdict(list)
    for t in txs:
        if t.amount < 0 and t.category == "bill":
            groups[t.description].append(t)
    bills = []
    for description, items in groups.items():
        if len(items) < 3:
            continue
        items.sort(key=lambda t: t.day)
        gaps = [(b.day - a.day).days for a, b in zip(items, items[1:])]
        common = dict(
            key=BILL_KEYS.get(description, description.lower().replace(" ", "_")),
            name=data.label(description),
            amount=round(-items[-1].amount, 2),
            kind="bill",
        )
        if all(g == 7 for g in gaps):
            bills.append(
                RecurringBill(
                    **common,
                    frequency="weekly",
                    last_paid=items[-1].day,
                    source=f"{len(items)} weekly payments in the last 4 months",
                )
            )
        elif all(25 <= g <= 35 for g in gaps):
            bills.append(
                RecurringBill(
                    **common,
                    day_of_month=Counter(t.day.day for t in items).most_common(1)[0][0],
                    source=f"{len(items)} monthly payments in the last 4 months",
                )
            )
    return bills


def _debts(persona: dict) -> list[RecurringBill]:
    return [
        RecurringBill(
            key="catalogue",
            name=d["creditor"],
            amount=d["monthly_payment"],
            day_of_month=d["day_of_month"],
            kind="debt",
            source=f"Direct Debit mandate; {d['missed_payments']} recent payments failed",
        )
        for d in persona["debts"]
    ]


def _wages(txs: list[data.Transaction]) -> WagePattern:
    pay = sorted((t for t in txs if t.category == "wages"), key=lambda t: t.day)
    amounts = [t.amount for t in pay]
    typical = median(amounts)
    recent = 0
    for a in reversed(amounts):
        if a < 0.8 * typical:
            recent += 1
        else:
            break
    usual = amounts[: len(amounts) - recent] or amounts
    recent_amounts = amounts[len(amounts) - recent :] or amounts[-1:]
    return WagePattern(
        weekday=Counter(t.day.weekday() for t in pay).most_common(1)[0][0],
        usual_weekly=round(mean(usual), 2),
        usual_sd=round(pstdev(usual), 2) if len(usual) > 1 else 0.0,
        recent_weekly=round(mean(recent_amounts), 2),
        recent_sd=round(pstdev(recent_amounts), 2) if len(recent_amounts) > 1 else 0.0,
        recent_weeks=recent,
        hours_cut=recent > 0,
        cut_since=pay[len(pay) - recent].day if recent else None,
    )


def _benefits(txs: list[data.Transaction]) -> list[FourWeeklyIncome]:
    groups: dict[str, list[data.Transaction]] = defaultdict(list)
    for t in txs:
        if t.category == "benefit":
            groups[t.description].append(t)
    out = []
    for description, items in groups.items():
        items.sort(key=lambda t: t.day)
        gaps = [(b.day - a.day).days for a, b in zip(items, items[1:])]
        if gaps and all(g == 28 for g in gaps):
            out.append(FourWeeklyIncome(data.label(description), items[-1].amount, items[-1].day))
    return out


def _spending(txs: list[data.Transaction], start: date, end: date) -> tuple[float, float]:
    per_day: dict[date, float] = defaultdict(float)
    for t in txs:
        if t.category == "spending":
            per_day[t.day] += -t.amount
    days = [per_day.get(start + timedelta(days=i), 0.0) for i in range((end - start).days + 1)]
    return round(mean(days), 2), round(pstdev(days), 2)


def build(txs: list[data.Transaction], persona: dict, today: date) -> Household:
    start = min(t.day for t in txs)
    spend_mean, spend_sd = _spending(txs, start, today)
    balance = data.opening_balance(txs) + sum(t.amount for t in txs if t.day <= today)
    return Household(
        today=today,
        balance=round(balance, 2),
        bills=tuple(
            sorted(
                _recurring_bills(txs) + _debts(persona),
                key=lambda b: (b.frequency != "weekly", b.day_of_month),
            )
        ),
        wages=_wages(txs),
        benefits=tuple(_benefits(txs)),
        daily_spend_mean=spend_mean,
        daily_spend_sd=spend_sd,
    )
