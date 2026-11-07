"""Web server: the bank's view, the customer's phone, and the API between them."""

from __future__ import annotations

import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, data, guard
from .call import CallSession
from .case import Case, World
from .firewall import BLOCKED_PURPOSES, POLICY, PurposeRefused

STATIC = Path(__file__).parent / "static"

app = FastAPI(title=config.PRODUCT_NAME)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

WORLD = World.demo()
BANK_VIEW = Case(WORLD)  # what the bank's monitoring sees before any call
STATE = {"safe_phrase": config.DEFAULT_SAFE_PHRASE}
CALLS: dict[str, CallSession] = {}
LOCK = threading.Lock()


class Phrase(BaseModel):
    safe_phrase: str


class Said(BaseModel):
    text: str


class Decision(BaseModel):
    send: bool


class GuardText(BaseModel):
    text: str


class Purpose(BaseModel):
    purpose: str


def _call(call_id: str) -> CallSession:
    call = CALLS.get(call_id)
    if call is None:
        raise HTTPException(404, "no such call")
    return call


def _reply(call: CallSession, events: list[dict]) -> dict:
    return {"events": events, "call": call.to_dict()}


def _mask(phrase: str) -> str:
    return " ".join(w[0] + "•" * (len(w) - 1) for w in phrase.split())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/overview")
def overview():
    case = BANK_VIEW
    hh, p = case.household, case.persona
    fc = case.forecast("customer_view")
    recent = [
        {"day": t.day.isoformat(), "description": data.label(t.description), "amount": t.amount}
        for t in WORLD.transactions[-14:]
    ][::-1]
    risk = fc.prob_shortfall_within(30)
    return {
        "mode": "offline" if config.offline_mode() else "live",
        "model": config.MODEL,
        "today": hh.today.isoformat(),
        "customer": {
            "name": p["full_name"],
            "age": p["age"],
            "city": p["city"],
            "household": f"{p['adults_in_home']} adult, child aged {p['children'][0]['age']}",
            "work": p["work"],
            "access_needs": p["access_needs"],
        },
        "balance": hh.balance,
        "wages": {
            "usual_weekly": hh.wages.usual_weekly,
            "recent_weekly": hh.wages.recent_weekly,
            "hours_cut": hh.wages.hours_cut,
            "since": hh.wages.cut_since.isoformat() if hh.wages.cut_since else None,
        },
        "bills": [
            {
                "name": b.name,
                "amount": b.amount,
                "when": "weekly" if b.frequency == "weekly" else f"day {b.day_of_month}",
                "kind": b.kind,
            }
            for b in hh.bills
        ],
        "recent_transactions": recent,
        "failed_payments": [{**f, "day": f["day"].isoformat()} for f in WORLD.failed_payments],
        "events": [e.to_dict() for e in case.events("customer_view")],
        "support": [s.to_dict() for s in case.support("customer_view")],
        "forecast": fc.to_dict(),
        "trigger": {
            "fire": risk >= config.TRIGGER_PROBABILITY,
            "risk_30d": round(risk, 3),
            "threshold": config.TRIGGER_PROBABILITY,
        },
        "safe_phrase_masked": _mask(STATE["safe_phrase"]),
        "data_use_policy": POLICY,
        "blocked_purposes": BLOCKED_PURPOSES,
    }


@app.get("/api/enrolment")
def get_enrolment():
    return {"safe_phrase": STATE["safe_phrase"], "masked": _mask(STATE["safe_phrase"])}


@app.post("/api/enrolment")
def set_enrolment(body: Phrase):
    phrase = re.sub(r"\s+", " ", body.safe_phrase).strip()
    words = re.findall(r"[A-Za-z']+", phrase)
    if len(words) < 2 or len(phrase) > 60 or not re.fullmatch(r"[A-Za-z' ]+", phrase):
        raise HTTPException(422, "Use 2 or more words, letters only, up to 60 characters.")
    STATE["safe_phrase"] = phrase
    return {"masked": _mask(phrase)}


@app.post("/api/calls")
def place_call():
    with LOCK:
        call = CallSession(WORLD, STATE["safe_phrase"])
        CALLS[call.id] = call
    return _reply(call, [{"type": "ringing"}])


@app.get("/api/calls/{call_id}")
def get_call(call_id: str):
    return _reply(_call(call_id), [])


@app.post("/api/calls/{call_id}/answer")
def answer(call_id: str):
    call = _call(call_id)
    return _reply(call, call.answer())


@app.post("/api/calls/{call_id}/decline")
def decline(call_id: str):
    call = _call(call_id)
    return _reply(call, call.decline())


@app.post("/api/calls/{call_id}/approve")
def approve(call_id: str):
    call = _call(call_id)
    return _reply(call, call.approve())


@app.post("/api/calls/{call_id}/say")
def say(call_id: str, body: Said):
    call = _call(call_id)
    if len(body.text) > 1000:
        raise HTTPException(422, "Too long.")
    return _reply(call, call.say(body.text))


@app.post("/api/calls/{call_id}/decision")
def decision(call_id: str, body: Decision):
    call = _call(call_id)
    return _reply(call, call.decide(body.send))


@app.post("/api/calls/{call_id}/hangup")
def hang_up(call_id: str):
    call = _call(call_id)
    return _reply(call, call.hang_up())


@app.post("/api/guard-check")
def guard_check(body: GuardText):
    """Show the speech guard on any sentence (for the demo)."""
    verdict = guard.check_utterance(body.text)
    return {"allowed": verdict.allowed, "spoken": verdict.text, "reason": verdict.reason}


@app.post("/api/firewall-check")
def firewall_check(body: Purpose):
    """Try to read the forecast for some purpose; only customer support is allowed."""
    try:
        BANK_VIEW.forecast(body.purpose)
        return {"allowed": True, "message": f"Allowed: {body.purpose}"}
    except PurposeRefused as e:
        return {"allowed": False, "message": str(e)}
