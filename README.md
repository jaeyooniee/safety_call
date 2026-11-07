# Safety Call

**We call before the crisis, not after.** A hackathon prototype for NatWest's
"AI-Powered Financial Inclusion & Resilience" challenge. Synthetic data only.

Today, banks phone struggling customers through collections, *after* they fall
behind, and the customers most at risk are the least likely to open their banking
app. Safety Call turns that around:

1. **Pre-crisis help call.** A household stress test (2,000 simulated futures over 90
   days) spots a likely shortfall weeks ahead. A voice agent phones the customer,
   explains two futures in plain words, prepares the right support, drafts the
   letters, and sends them only after the customer says yes. When a person is
   needed, it hands over the whole case file, so the customer never repeats their story.
2. **Reverse authentication.** A bank calling about money sounds like a scam. So the
   bank proves itself first: every call opens with the safe phrase the customer chose,
   spoken by the system (the AI never sees it), and the agent can never ask for a PIN,
   password, card number or code. "Unsure? Hang up and call 159."
3. **Life-event detection.** Transactions are read for what happened (hours cut, wages
   stopped, a pharmacy spike, baby goods, failed Direct Debits), and each event
   prepares its own support bundle before the call starts.

### Ethics, enforced in code

| Promise | Where it is enforced |
|---|---|
| Your forecast is yours: never used for lending, credit limits, scoring, pricing, marketing or collections | `firewall.py`: insights are stored with one allowed purpose; any other read is refused and logged |
| Guidance, not advice | Agent prompt and support results: options, illustrative rules, "the organisation decides" |
| Nothing sent without your yes | `call.py`: the model can only *request* a send; the code records the customer's own yes or no |
| Say "that's wrong" and it is fixed | `correct_information` tool / `Case.correct`: recalculates and withdraws drafts built on the old fact |
| Every figure has a source | `facts.py`: each £, % and date the agent speaks is traced to a tool result ("Why?" panel); untraced figures are flagged |
| The safe phrase never reaches the AI | `guard.py`: redacted from the customer's words, and every request is checked before it is sent |

## Run it

Python 3.11+. From this folder:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

(macOS/Linux: `.venv/bin/python`.) Then start the app and open http://127.0.0.1:8000:

```bash
.venv/Scripts/python -m safety_call
```

- **Offline (default without a key):** a scripted agent that uses the same tools, so
  every figure is still computed. Safe for a demo with bad Wi-Fi.
- **Live Claude:** set `ANTHROPIC_API_KEY` (or `SAFETY_CALL_OFFLINE=0`). Uses
  `claude-opus-5-5` at effort `low` for quick spoken replies, strict tool use, and
  server-side refusal fallbacks. Override with `SAFETY_CALL_MODEL` / `SAFETY_CALL_EFFORT`.

Voice: the browser reads everything aloud; **Speak** uses speech recognition
(Chrome or Edge). Typing always works too.

## Check it

```bash
.venv/Scripts/python -m pytest -q
```

```bash
.venv/Scripts/python -m safety_call.demo
```

The demo script plays the whole call with typed input and checks: the call opens with
the safe phrase, letters go only after a yes, the actions lower the risk, and every
figure has a source. Add `--live` to run it against the Claude API.

## 90-second demo script

1. **Bank view (10 s).** "Amira, a care worker with low vision. Her agency cut her
   hours three weeks ago. The bank sees it: *life event: hours cut*, and two failed
   Direct Debits. The stress test gives a 58% chance she goes below zero by 26 November,
   when energy and Council Tax land together. Today the bank would wait for arrears.
   We call now."
2. **Place help call, then Answer (15 s).** The system, not the AI, speaks her safe
   phrase and says we will never ask for a PIN or codes, and that she can call 159.
   She approves in her app.
3. **The call (35 s).** The agent explains, offers two futures, finds the Council Tax
   single person discount she isn't getting and an affordable energy plan, and reads
   each letter's summary. Click a figure and the **Why?** panel shows its source.
4. **Consent (10 s).** "Shall I send them?" Yes: sent. The fan chart redraws: the risk
   drops to about a third, and the typical path stays above zero.
5. **Warm handover (10 s).** The case file goes to a debt adviser with her consent:
   Universal Credit and Breathing Space, without her repeating anything.
6. **Safeguards (10 s).** Try "What's your PIN?" in the speech guard: blocked. Try
   using the forecast for a credit limit: refused. Optional: say "That's wrong, I live
   with my partner" during a call and watch it correct itself.

## Layout

```
safety_call/
  data.py         synthetic customer and four months of transactions
  household.py    recurring bills, wage pattern, benefits, spending
  life_events.py  events from transactions, each with a support bundle
  simulator.py    the household stress test (Monte Carlo, shocks, actions)
  support.py      illustrative eligibility rules
  letters.py      letters and the adviser case file, from records only
  firewall.py     prediction firewall (purpose-limited insights)
  guard.py        speech guard, safe-phrase redaction and leak check
  facts.py        facts with sources, and tracing spoken figures
  case.py         one customer's case: drafts, consent, corrections, timeline
  tools.py        the agent's tools
  agent.py        live Claude agent and offline scripted agent
  call.py         the call: opening, consent gate, guards around the model
  server.py       FastAPI app; static/ holds the web page
tests/            47 tests (engines, life events, safeguards, call flow, API)
```

## Simulated or stubbed

Phone network (an in-browser call), sending letters, the adviser's follow-up, the
banking-app approval, and the bank connection. Support rules are simplified
summaries of public UK rules, not a benefits calculator; the organisations decide.
