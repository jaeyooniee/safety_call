"""Household stress test: Monte Carlo over the next 90 days.

Banks are stress-tested; families are not. This runs a few thousand possible
futures of one household's balance, with the shocks that typically hit it, and
answers: how likely is the balance to go below zero, when, which shock breaks
the budget first, and what a set of actions changes.

The same random draws are reused for every run (common random numbers), so the
difference between "do nothing" and "act now" comes from the actions alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from . import config
from .household import Household

# What the customer could do, and what each one changes in the forecast.
ENERGY_PLAN_MONTHS = 3


def energy_plan_amount(current: float) -> float:
    """A proposed affordable Direct Debit: about 60% of today's, rounded down to £5."""
    return float(int(current * 0.6 // 5 * 5))


ACTIONS = {
    "single_person_discount": "Council Tax 25% single person discount",
    "energy_payment_plan": "Lower energy Direct Debit for 3 months",
}

# Shocks the stress test applies. Probabilities are illustrative assumptions.
SHOCKS = {
    "hours_stay_cut": "Working hours stay cut",
    "appliance_repair": "A boiler or appliance repair",
    "child_cost": "An unexpected cost for her child",
    "energy_price_rise": "Energy prices rise 10% from January",
}


@dataclass(frozen=True)
class Assumptions:
    horizon_days: int = config.HORIZON_DAYS
    paths: int = config.PATHS
    seed: int = config.SEED
    weekly_recovery_probability: float = 0.3  # chance each week that hours come back
    appliance_probability: float = 0.10
    appliance_cost: tuple[float, float] = (150.0, 300.0)
    child_cost_probability: float = 0.35
    child_cost: tuple[float, float] = (20.0, 60.0)
    energy_rise_probability: float = 0.15
    energy_rise: float = 0.10
    energy_rise_from: date = date(2027, 1, 1)


@dataclass
class Forecast:
    today: date
    dates: list[date]
    actions: tuple[str, ...]
    p10: np.ndarray
    p25: np.ndarray
    p50: np.ndarray
    p75: np.ndarray
    p90: np.ndarray
    prob_below_on_day: np.ndarray  # P(balance < 0 on that day)
    prob_below_by_day: np.ndarray  # P(balance went below 0 at any point up to that day)
    drivers: list[dict] = field(default_factory=list)

    def prob_shortfall_within(self, days: int) -> float:
        return float(self.prob_below_by_day[min(days, len(self.dates)) - 1])

    def prob_shortfall_by(self, day: date) -> float:
        i = (day - self.today).days - 1
        i = max(0, min(i, len(self.dates) - 1))
        return float(self.prob_below_by_day[i])

    def peak_risk(self, within_days: int = 30) -> tuple[date, float]:
        window = self.prob_below_on_day[:within_days]
        i = int(np.argmax(window))
        return self.dates[i], float(window[i])

    def lowest_median(self, within_days: int = 30) -> tuple[date, float]:
        window = self.p50[:within_days]
        i = int(np.argmin(window))
        return self.dates[i], float(window[i])

    def to_dict(self) -> dict:
        peak_day, peak_p = self.peak_risk()
        low_day, low_v = self.lowest_median()
        return {
            "today": self.today.isoformat(),
            "dates": [d.isoformat() for d in self.dates],
            "actions": list(self.actions),
            "bands": {
                name: [round(float(v), 2) for v in arr]
                for name, arr in (
                    ("p10", self.p10),
                    ("p25", self.p25),
                    ("p50", self.p50),
                    ("p75", self.p75),
                    ("p90", self.p90),
                )
            },
            "prob_shortfall_30d": round(self.prob_shortfall_within(30), 3),
            "prob_shortfall_90d": round(self.prob_shortfall_within(len(self.dates)), 3),
            "peak_risk_day": peak_day.isoformat(),
            "peak_risk_probability": round(peak_p, 3),
            "lowest_median_day": low_day.isoformat(),
            "lowest_median_balance": round(low_v, 2),
            "drivers": self.drivers,
        }


def _schedule(hh: Household, dates: list[date], actions: tuple[str, ...], a: Assumptions):
    """Known money in and out per day, before any randomness."""
    n = len(dates)
    index = {d: i for i, d in enumerate(dates)}
    fixed = np.zeros(n)
    energy_days = np.zeros(n)  # energy bill amounts, for the price-rise shock
    end = dates[-1]
    for bill in hh.bills:
        for k, due in enumerate(bill.due_dates(hh.today, end)):
            amount = bill.amount
            if bill.key == "council_tax" and "single_person_discount" in actions:
                amount = round(amount * 0.75, 2)
            if bill.key == "energy":
                if "energy_payment_plan" in actions and k < ENERGY_PLAN_MONTHS:
                    amount = energy_plan_amount(bill.amount)
                if due >= a.energy_rise_from:
                    energy_days[index[due]] += amount
            fixed[index[due]] -= amount
    for benefit in hh.benefits:
        due = benefit.last_paid + timedelta(days=28)
        while due <= end:
            if due in index:
                fixed[index[due]] += benefit.amount
            due += timedelta(days=28)
    return fixed, energy_days


def simulate(
    hh: Household,
    actions: tuple[str, ...] | list[str] = (),
    assumptions: Assumptions = Assumptions(),
    disable: frozenset[str] = frozenset(),
    with_drivers: bool = True,
) -> Forecast:
    a = assumptions
    actions = tuple(sorted(set(actions)))
    unknown = set(actions) - set(ACTIONS)
    if unknown:
        raise ValueError(f"unknown actions: {sorted(unknown)}")
    dates = [hh.today + timedelta(days=i) for i in range(1, a.horizon_days + 1)]
    n, p = len(dates), a.paths
    rng = np.random.default_rng(a.seed)

    # Draw every random number up front, in a fixed order (common random numbers).
    fridays = [i for i, d in enumerate(dates) if d.weekday() == hh.wages.weekday]
    recover_u = rng.random((p, len(fridays)))
    wage_z = rng.standard_normal((p, len(fridays)))
    spend_z = rng.standard_normal((p, n))
    appl_u, appl_day, appl_cost = rng.random(p), rng.integers(0, n, p), rng.uniform(*a.appliance_cost, p)
    child_u, child_day, child_cost = rng.random(p), rng.integers(0, n, p), rng.uniform(*a.child_cost, p)
    rise_u = rng.random(p)

    fixed, energy_days = _schedule(hh, dates, actions, a)
    flows = np.tile(fixed, (p, 1))

    # Wages: hours are cut now; each week they may come back.
    w = hh.wages
    recovered = np.zeros(p, dtype=bool)
    for k, i in enumerate(fridays):
        if "hours_stay_cut" in disable:
            recovered[:] = True
        else:
            recovered |= recover_u[:, k] < a.weekly_recovery_probability
        if w.hours_cut:
            pay = np.where(
                recovered,
                w.usual_weekly + w.usual_sd * wage_z[:, k],
                w.recent_weekly + max(w.recent_sd, 5.0) * wage_z[:, k],
            )
        else:
            pay = w.usual_weekly + w.usual_sd * wage_z[:, k]
        flows[:, i] += np.clip(pay, 0, None)

    flows -= np.clip(hh.daily_spend_mean + hh.daily_spend_sd * spend_z, 0, None)

    rows = np.arange(p)
    if "appliance_repair" not in disable:
        hit = appl_u < a.appliance_probability
        np.subtract.at(flows, (rows[hit], appl_day[hit]), appl_cost[hit])
    if "child_cost" not in disable:
        hit = child_u < a.child_cost_probability
        np.subtract.at(flows, (rows[hit], child_day[hit]), child_cost[hit])
    if "energy_price_rise" not in disable:
        hit = rise_u < a.energy_rise_probability
        flows[hit] -= energy_days * a.energy_rise

    balance = hh.balance + np.cumsum(flows, axis=1)
    below = balance < 0
    q = np.percentile(balance, [10, 25, 50, 75, 90], axis=0)
    forecast = Forecast(
        today=hh.today,
        dates=dates,
        actions=actions,
        p10=q[0],
        p25=q[1],
        p50=q[2],
        p75=q[3],
        p90=q[4],
        prob_below_on_day=below.mean(axis=0),
        prob_below_by_day=np.maximum.accumulate(below, axis=1).mean(axis=0),
    )
    if with_drivers:
        base = forecast.prob_shortfall_within(30)
        drivers = []
        for key, name in SHOCKS.items():
            without = simulate(hh, actions, a, disable | {key}, with_drivers=False)
            drivers.append(
                {
                    "shock": key,
                    "name": name,
                    "risk_reduction_30d": round(base - without.prob_shortfall_within(30), 3),
                }
            )
        drivers.sort(key=lambda d: d["risk_reduction_30d"], reverse=True)
        forecast.drivers = drivers
    return forecast
