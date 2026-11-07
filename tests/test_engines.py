from dataclasses import replace
from datetime import date, timedelta

from safety_call import data, household, life_events, simulator, support
from safety_call.case import Case, World


def demo():
    txs = data.generate_transactions()
    return txs, household.build(txs, data.PERSONA, data.DEMO_TODAY)


def test_household_finds_bills_wages_and_benefits():
    _, hh = demo()
    assert hh.balance == data.CLOSING_BALANCE
    energy, ct, rent = hh.bill("energy"), hh.bill("council_tax"), hh.bill("rent")
    assert (energy.amount, energy.day_of_month) == (118.0, 24)
    assert (ct.amount, ct.day_of_month) == (138.0, 24)
    assert rent.frequency == "weekly" and rent.amount == 90.0
    assert hh.bill("catalogue").kind == "debt"
    assert hh.wages.hours_cut and hh.wages.recent_weeks == 3
    assert hh.wages.cut_since == data.HOURS_CUT_FROM
    assert hh.benefits[0].amount == data.CHILD_BENEFIT


def test_demo_life_events_are_reduced_hours_and_missed_payments():
    txs, hh = demo()
    events = life_events.detect(txs, hh, data.FAILED_PAYMENTS, data.DEMO_TODAY)
    assert [e.key for e in events] == ["reduced_hours", "payment_difficulty"]
    assert "universal_credit" in events[0].bundle


def _with(txs, extra):
    return sorted(txs + extra, key=lambda t: t.day)


def test_job_loss_when_wages_stop():
    txs, _ = demo()
    cutoff = data.DEMO_TODAY - timedelta(days=20)
    txs = [t for t in txs if not (t.category == "wages" and t.day > cutoff)]
    hh = household.build(txs, data.PERSONA, data.DEMO_TODAY)
    keys = [e.key for e in life_events.detect(txs, hh, [], data.DEMO_TODAY)]
    assert "job_loss" in keys and "reduced_hours" not in keys


def test_illness_when_pharmacy_spending_spikes():
    txs, _ = demo()
    spike = [data.Transaction(data.DEMO_TODAY - timedelta(days=d), "PHARMACY", -18.0, "spending") for d in (3, 9, 15, 21)]
    txs = _with(txs, spike)
    hh = household.build(txs, data.PERSONA, data.DEMO_TODAY)
    events = {e.key: e for e in life_events.detect(txs, hh, [], data.DEMO_TODAY)}
    assert "illness" in events
    assert "statutory_sick_pay" in events["illness"].bundle


def test_new_baby_when_baby_goods_appear():
    txs, _ = demo()
    baby = [
        data.Transaction(data.DEMO_TODAY - timedelta(days=d), "BABY STORE", -32.0, "spending") for d in (5, 20, 40)
    ]
    txs = _with(txs, baby)
    hh = household.build(txs, data.PERSONA, data.DEMO_TODAY)
    assert "new_baby" in {e.key for e in life_events.detect(txs, hh, [], data.DEMO_TODAY)}


def test_no_events_for_a_steady_household():
    txs, _ = demo()
    steady = [replace(t, amount=310.0) if t.category == "wages" else t for t in txs]
    hh = household.build(steady, data.PERSONA, data.DEMO_TODAY)
    assert life_events.detect(steady, hh, [], data.DEMO_TODAY) == []


def test_stress_test_is_repeatable_and_actions_lower_risk():
    _, hh = demo()
    a, b = simulator.simulate(hh), simulator.simulate(hh)
    assert a.prob_shortfall_within(30) == b.prob_shortfall_within(30)
    after = simulator.simulate(hh, ("single_person_discount", "energy_payment_plan"))
    assert a.prob_shortfall_within(30) >= 0.5  # the trigger fires for the demo customer
    assert after.prob_shortfall_within(30) < a.prob_shortfall_within(30) - 0.15
    assert a.lowest_median()[1] < 0 < after.lowest_median()[1]
    assert a.drivers[0]["shock"] == "hours_stay_cut"
    assert all(a.prob_below_by_day[i] <= a.prob_below_by_day[i + 1] for i in range(len(a.dates) - 1))


def test_stress_test_rejects_unknown_actions():
    _, hh = demo()
    try:
        simulator.simulate(hh, ("free_money",))
    except ValueError:
        return
    raise AssertionError("unknown action accepted")


def test_support_bundle_for_demo_customer():
    txs, hh = demo()
    events = life_events.detect(txs, hh, data.FAILED_PAYMENTS, data.DEMO_TODAY)
    results = {s.id: s for s in support.check(data.PERSONA, hh, events)}
    assert results["council_tax_single_person_discount"].status == "eligible"
    assert results["council_tax_single_person_discount"].monthly_effect == 34.5
    assert results["energy_payment_plan"].monthly_effect == 48.0
    assert results["universal_credit"].status == "may_be_eligible"
    assert results["universal_credit"].monthly_effect is None  # no invented amount
    assert "breathing_space" in results


def test_correction_changes_support_and_withdraws_drafts():
    case = Case(World.demo())
    from safety_call import letters

    case.add_draft(letters.council_tax_spd("d1", case.persona, case.household, case.household.today))
    record = case.correct("adults_in_home", "2")
    assert record["before"] == "1" and record["after"] == "2"
    assert case.drafts["d1"].status == "superseded"
    spd = next(s for s in case.support() if s.id == "council_tax_single_person_discount")
    assert spd.status == "not_eligible"
    # The bank's shared record is untouched: corrections live in this case only.
    assert data.PERSONA["adults_in_home"] == 1


def test_hours_back_correction_lowers_risk():
    case = Case(World.demo())
    before = case.forecast().prob_shortfall_within(30)
    case.correct("hours_back_to_normal", "yes")
    assert case.forecast().prob_shortfall_within(30) < before
    assert "reduced_hours" not in {e.key for e in case.events()}
