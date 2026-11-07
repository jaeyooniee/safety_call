import os

os.environ["SAFETY_CALL_OFFLINE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from safety_call.server import app  # noqa: E402

client = TestClient(app)


def test_overview_and_trigger():
    o = client.get("/api/overview").json()
    assert o["mode"] == "offline"
    assert o["trigger"]["fire"] is True
    assert {e["key"] for e in o["events"]} == {"reduced_hours", "payment_difficulty"}
    assert "blue" not in o["safe_phrase_masked"]
    assert len(o["forecast"]["bands"]["p50"]) == 90
    assert client.get("/").status_code == 200


def test_full_call_over_http():
    r = client.post("/api/calls").json()
    cid = r["call"]["id"]
    ev = client.post(f"/api/calls/{cid}/answer").json()["events"]
    assert ev[0]["has_safe_phrase"]
    ev = client.post(f"/api/calls/{cid}/approve").json()["events"]
    assert any(e["type"] == "agent_say" for e in ev)
    for text in ["Yes, I can talk.", "Yes please."]:
        client.post(f"/api/calls/{cid}/say", json={"text": text})
    r = client.post(f"/api/calls/{cid}/decision", json={"send": True}).json()
    assert {d["status"] for d in r["call"]["drafts"]} == {"sent"}
    assert r["call"]["after"]["prob_shortfall_30d"] < r["call"]["forecast"]["prob_shortfall_30d"]
    assert client.post(f"/api/calls/{cid}/hangup").json()["call"]["status"] == "ended"


def test_enrolment_validation():
    assert client.post("/api/enrolment", json={"safe_phrase": "x"}).status_code == 422
    assert client.post("/api/enrolment", json={"safe_phrase": "red bike in April"}).status_code == 200
    assert client.get("/api/enrolment").json()["safe_phrase"] == "red bike in April"
    client.post("/api/enrolment", json={"safe_phrase": "blue kettle on Sunday"})


def test_guard_and_firewall_endpoints():
    assert client.post("/api/guard-check", json={"text": "What's your PIN?"}).json()["allowed"] is False
    assert client.post("/api/firewall-check", json={"purpose": "credit_limit"}).json()["allowed"] is False
    assert client.post("/api/firewall-check", json={"purpose": "customer_support"}).json()["allowed"] is True
