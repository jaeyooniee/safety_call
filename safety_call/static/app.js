"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n < 0 ? "-£" : "£") + Math.abs(n).toFixed(2);
const pct = (p) => Math.round(p * 100) + "%";
const niceDate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "long" });

let overview = null;
let callId = null;
let lastCall = null;
let phraseShown = false;

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// ---------------------------------------------------------------- speech out
const speech = {
  queue: [],
  speaking: false,
  voice: null,
  enabled() { return $("voice-on").checked && "speechSynthesis" in window; },
  // Natural (neural) voices first: Edge's "Online (Natural)" voices, then Google's, then the rest.
  score(v) {
    let s = 0;
    if (/natural/i.test(v.name)) s += 100;
    if (/online/i.test(v.name)) s += 40;
    if (/^google/i.test(v.name)) s += 60;
    if (/sonia|libby|ryan|maisie|thomas/i.test(v.name)) s += 15;  // UK neural voices in Edge
    if (v.lang === "en-GB") s += 30;
    else if (v.lang.startsWith("en")) s += 5;
    if (/hazel|george|susan|david|zira|mark/i.test(v.name) && !/natural/i.test(v.name)) s -= 50;  // older robotic voices
    return s;
  },
  english() {
    const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    return voices.filter((v) => v.lang.startsWith("en")).sort((a, b) => this.score(b) - this.score(a));
  },
  pickVoice() {
    const voices = this.english();
    let saved = null;
    try { saved = localStorage.getItem("safety-call-voice"); } catch (_) {}
    this.voice = voices.find((v) => v.name === saved) || voices[0] || null;
    const select = $("voice-select");
    if (!select) return;
    select.innerHTML = voices.length
      ? voices.map((v) => `<option value="${esc(v.name)}">${esc(v.name.replace(/^Microsoft /, ""))}</option>`).join("")
      : `<option value="">No voices in this browser</option>`;
    select.disabled = !voices.length;
    if (this.voice) select.value = this.voice.name;
  },
  choose(name) {
    this.voice = this.english().find((v) => v.name === name) || this.voice;
    try { localStorage.setItem("safety-call-voice", name); } catch (_) {}
  },
  say(text) {
    if (!this.enabled()) return;
    // One sentence at a time sounds more natural and avoids cut-offs on long lines.
    for (const part of text.match(/[^.!?]+[.!?]+["']?|\S[^.!?]*$/g) || [text]) {
      if (part.trim()) this.queue.push(part.trim());
    }
    if (!this.speaking) this.next();
  },
  next() {
    const text = this.queue.shift();
    if (!text) { this.speaking = false; return; }
    this.speaking = true;
    const u = new SpeechSynthesisUtterance(text);
    if (this.voice) u.voice = this.voice;
    u.lang = this.voice ? this.voice.lang : "en-GB";
    u.rate = 1.0;
    u.pitch = 1.0;
    u.onend = u.onerror = () => this.next();
    speechSynthesis.speak(u);
  },
  stop() { this.queue = []; this.speaking = false; if (window.speechSynthesis) speechSynthesis.cancel(); },
};
if ("speechSynthesis" in window) {
  speech.pickVoice();
  speechSynthesis.onvoiceschanged = () => speech.pickVoice();
}

// ---------------------------------------------------------------- speech in
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
function toggleMic() {
  const btn = $("mic");
  if (!Recognition) {
    $("mic-msg").textContent = "Speech input needs Chrome or Edge. You can type instead.";
    return;
  }
  if (recognizer) { recognizer.stop(); return; }
  speech.stop();
  recognizer = new Recognition();
  recognizer.lang = "en-GB";
  recognizer.interimResults = true;
  recognizer.maxAlternatives = 1;
  btn.setAttribute("aria-pressed", "true");
  btn.textContent = "Listening… stop";
  $("mic-msg").textContent = "Listening.";
  let finalText = "";
  recognizer.onresult = (e) => {
    let interim = "";
    for (const r of e.results) (r.isFinal ? (finalText = r[0].transcript) : (interim = r[0].transcript));
    $("say-input").value = finalText || interim;
  };
  recognizer.onerror = (e) => { $("mic-msg").textContent = "Couldn't hear that (" + e.error + "). You can type instead."; };
  recognizer.onend = () => {
    btn.setAttribute("aria-pressed", "false");
    btn.textContent = "Speak";
    recognizer = null;
    const text = $("say-input").value.trim();
    if (finalText && text) { $("mic-msg").textContent = ""; send(text); }
  };
  recognizer.start();
}

// ---------------------------------------------------------------- bank view
function renderCustomer(o) {
  const c = o.customer;
  $("customer").innerHTML = `<dl>
    <dt>Name</dt><dd>${esc(c.name)}, ${c.age}, ${esc(c.city)}</dd>
    <dt>Household</dt><dd>${esc(c.household)}</dd>
    <dt>Work</dt><dd>${esc(c.work)}</dd>
    <dt>Access needs</dt><dd>${esc(c.access_needs)}</dd>
    <dt>Balance today</dt><dd>${money(o.balance)} (${niceDate(o.today)})</dd>
  </dl>`;
}

function renderEvents(events, support) {
  const byEvent = {};
  for (const s of support) (byEvent[s.for_event] ||= []).push(s);
  $("events").innerHTML = events.length ? events.map((e) => `
    <div class="event">
      <h4>${esc(e.name)} <span class="badge">${esc(e.confidence)} confidence</span></h4>
      <ul>${e.evidence.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <p class="bundle">Support prepared: ${(byEvent[e.key] || []).map((s) =>
        `${esc(s.name)}<span class="badge ${esc(s.status)}">${esc(s.status.replaceAll("_", " "))}</span>`).join(", ") || "none"}</p>
    </div>`).join("") : `<p class="muted">No life events detected.</p>`;
}

function renderForecast(base, after) {
  const svg = $("fan");
  const W = 640, H = 300, L = 56, R = 12, T = 14, B = 34;
  const n = base.dates.length;
  const all = [...base.bands.p10, ...base.bands.p90, ...(after ? [...after.bands.p10, ...after.bands.p90] : []), 0];
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
  const x = (i) => L + (i / (n - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const band = (lo_, hi_) => "M" + lo_.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L") + "L" +
    hi_.slice().reverse().map((v, i) => `${x(n - 1 - i).toFixed(1)},${y(v).toFixed(1)}`).join("L") + "Z";
  const line = (a) => "M" + a.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L");

  const ticks = [];
  const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / 4)));
  const nice = [1, 2, 2.5, 5, 10].map((m) => m * step).find((s) => (hi - lo) / s <= 6);
  for (let v = Math.ceil(lo / nice) * nice; v <= hi; v += nice) ticks.push(v);

  const peakIdx = base.dates.indexOf(base.peak_risk_day);
  const layer = (f, cls, colour, opacity) => `
    <g class="${cls}">
      <path d="${band(f.bands.p10, f.bands.p90)}" fill="${colour}" opacity="${opacity * 0.35}"/>
      <path d="${band(f.bands.p25, f.bands.p75)}" fill="${colour}" opacity="${opacity * 0.55}"/>
      <path d="${line(f.bands.p50)}" fill="none" stroke="${colour}" stroke-width="2.5"/>
    </g>`;
  const css = getComputedStyle(document.documentElement);
  const c = (v) => css.getPropertyValue(v).trim();

  const title = svg.querySelector("title").outerHTML;
  const descText = `Typical balance falls to ${money(base.lowest_median_balance)} around ${niceDate(base.lowest_median_day)}. ` +
    `Chance of going below zero within 30 days: ${pct(base.prob_shortfall_30d)} if nothing changes` +
    (after ? `, ${pct(after.prob_shortfall_30d)} with the agreed actions.` : ".");
  svg.innerHTML = `${title}<desc id="fan-desc">${esc(descText)}</desc>
    <rect x="${L}" y="${y(0)}" width="${W - L - R}" height="${Math.max(0, H - B - y(0))}" fill="${c("--zero-zone")}"/>
    ${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${c("--border")}" stroke-width="1"/>
      <text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v < 0 ? "-" : ""}£${Math.abs(v)}</text>`).join("")}
    <line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="${c("--danger")}" stroke-width="1.5"/>
    ${layer(base, "base-layer", c("--base-band"), after ? 0.8 : 1)}
    ${after ? layer(after, "after-layer", c("--after-band"), 1) : ""}
    ${peakIdx >= 0 ? `<line x1="${x(peakIdx)}" x2="${x(peakIdx)}" y1="${T}" y2="${H - B}" stroke="${c("--danger")}" stroke-dasharray="4 4"/>
      <text x="${Math.min(x(peakIdx) + 4, W - 120)}" y="${T + 12}">Riskiest: ${niceDate(base.peak_risk_day)}</text>` : ""}
    ${[0, 15, 30, 45, 60, 75, 89].map((i) => `<text x="${x(i)}" y="${H - 12}" text-anchor="middle">${niceDate(base.dates[i]).replace(/ (\w{3})\w*/, " $1")}</text>`).join("")}
  `;
  $("legend-after").hidden = !after;
  $("risk-summary").innerHTML = `Chance of going below zero within 30 days: <strong class="bad">${pct(base.prob_shortfall_30d)}</strong> if nothing changes` +
    (after ? `, <strong class="good">${pct(after.prob_shortfall_30d)}</strong> with the agreed actions.` : ".");
  const d = (after || base).drivers.filter((x) => x.risk_reduction_30d > 0.004);
  $("drivers").innerHTML = d.length ? `<p class="hint">What drives the risk${after ? " now" : ""} (risk removed if this shock didn't happen):</p>
    <ol class="drivers">${d.map((x) => `<li>${esc(x.name)}: ${pct(x.risk_reduction_30d)}</li>`).join("")}</ol>` : "";
}

function renderTrigger(o) {
  const t = o.trigger;
  $("trigger").innerHTML = t.fire
    ? `<p><strong>Help call trigger:</strong> ${pct(t.risk_30d)} chance of going below zero in 30 days (threshold ${pct(t.threshold)}). Call now, before arrears.</p>
       <button class="btn" id="place">Place help call</button>`
    : `<p>No help call needed: risk ${pct(t.risk_30d)} is under ${pct(t.threshold)}.</p>`;
  if (t.fire) $("place").addEventListener("click", placeCall);
}

function renderTxs(o) {
  $("txs").innerHTML = `<table class="txs"><thead><tr><th>Date</th><th>Description</th><th class="num">Amount</th></tr></thead><tbody>
    ${o.recent_transactions.map((t) => `<tr><td>${niceDate(t.day)}</td><td>${esc(t.description)}</td><td class="num ${t.amount > 0 ? "in" : ""}">${money(t.amount)}</td></tr>`).join("")}
    </tbody></table>
    <p class="hint">Failed Direct Debits: ${o.failed_payments.map((f) => `${esc(f.payee)} ${money(f.amount)} on ${niceDate(f.day)}`).join("; ")}</p>
    <table class="txs"><thead><tr><th>Regular bill</th><th>When</th><th class="num">Amount</th></tr></thead><tbody>
    ${o.bills.map((b) => `<tr><td>${esc(b.name)}</td><td>${esc(b.when)}</td><td class="num">${money(b.amount)}</td></tr>`).join("")}</tbody></table>`;
}

function renderSafeguards(o) {
  $("phrase-masked").textContent = o.safe_phrase_masked;
  $("policy").textContent = o.data_use_policy;
  $("fw-purpose").innerHTML = `<option value="customer_support">customer support (allowed)</option>` +
    Object.entries(o.blocked_purposes).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  $("fw-purpose").value = "credit_limit";
}

async function loadOverview() {
  overview = await api("/api/overview");
  $("mode").textContent = overview.mode === "live" ? `Agent: Claude (${overview.model})` : "Agent: offline script (no API key)";
  renderCustomer(overview);
  renderEvents(overview.events, overview.support);
  renderForecast(overview.forecast, null);
  renderTrigger(overview);
  renderTxs(overview);
  renderSafeguards(overview);
}

// ---------------------------------------------------------------- case view
function renderCase(call) {
  lastCall = call;
  const tl = call.timeline;
  $("timeline").innerHTML = tl.length ? tl.map((e) =>
    `<li class="tl-${esc(e.kind)}"><time>${esc(e.at)}</time><span class="tl-kind">${esc(e.kind)}</span> ${esc(e.text)}</li>`).join("") : `<li class="muted">No call yet.</li>`;
  $("drafts").innerHTML = call.drafts.length ? call.drafts.map((d) => `
    <details class="draft">
      <summary><strong>${esc(d.subject)}</strong> to ${esc(d.recipient)}<span class="status ${esc(d.status)}">${esc(d.status.replaceAll("_", " "))}</span></summary>
      <pre>${esc(d.body)}</pre>
    </details>`).join("") : `<p class="muted">Nothing drafted yet.</p>`;
  renderEvents(call.events, call.support);
  renderForecast(call.forecast, call.after);

  const pending = call.drafts.filter((d) => d.status === "awaiting_confirmation");
  $("confirm-box").hidden = !pending.length;
  if (pending.length) {
    $("confirm-text").innerHTML = `<strong>Send?</strong> ${pending.map((d) => `${esc(d.subject)} to ${esc(d.recipient)}`).join("; ")}` +
      (pending.some((d) => d.shares_personal_data) ? `<br><span class="hint">This shares your personal information.</span>` : "");
  }
  const s = call.status;
  $("phone-status").textContent = { ringing: "Incoming call", verifying: "Checking it's really your bank", in_call: "In call · Safety Call", ended: "Call ended", declined: "Call declined" }[s] || s;
  $("approve-box").hidden = s !== "verifying";
  for (const id of ["say-input", "mic"]) $(id).disabled = !(s === "in_call" || s === "verifying");
  $("hangup").disabled = !(s === "in_call" || s === "verifying");
}

function addMsg(cls, who, html) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.innerHTML = (who ? `<span class="who">${esc(who)}</span>` : "") + html;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

const TOOL_LABELS = {
  get_situation: "Checked the situation and forecast",
  check_support: "Checked the support bundle",
  compare_futures: "Compared two futures",
  draft_letter: "Drafted a letter",
  prepare_adviser_handover: "Prepared the adviser case file",
  request_send: "Asked for your yes or no",
  correct_information: "Corrected our records",
};

function renderWhy(figures) {
  if (!figures || !figures.length) return;
  $("why").innerHTML = `<table><thead><tr><th>Figure</th><th>What it is</th><th>Source</th></tr></thead><tbody>
    ${figures.map((f) => f.fact
      ? `<tr><td>${esc(f.figure)}</td><td>${esc(f.fact.label)} (${esc(f.fact.value)})</td><td>${esc(f.fact.source)}</td></tr>`
      : `<tr><td class="untraced">${esc(f.figure)}</td><td colspan="2" class="untraced">No source found: flagged</td></tr>`).join("")}
    </tbody></table>`;
}

function handle(events) {
  for (const e of events) {
    switch (e.type) {
      case "system_say":
        addMsg("system", "System (not the AI)", esc(e.text));
        speech.say(e.text);
        break;
      case "agent_say":
        addMsg("agent", "Safety Call agent", esc(e.text));
        speech.say(e.text);
        renderWhy(e.figures);
        break;
      case "customer_say":
        addMsg("customer", "Amira", esc(e.text));
        break;
      case "tool":
        addMsg("tool", "", "· " + esc(TOOL_LABELS[e.name] || e.name));
        break;
      case "guard_block":
        addMsg("blocked", "Speech guard", "Blocked before it was spoken: " + esc(e.reason) + (e.original ? `<br><s>${esc(e.original)}</s>` : ""));
        break;
      case "sent":
        addMsg("tool", "", "· Sent after your yes");
        break;
      case "cancelled":
        addMsg("tool", "", "· Not sent");
        break;
      case "error":
        addMsg("error", "Connection", esc(e.message));
        break;
      case "call_ended":
        addMsg("tool", "", "· Call ended");
        // The agent said goodbye: leave its last words on screen for a moment.
        if (e.reason === "finished") {
          const endedId = callId;
          setTimeout(() => { if (callId === endedId) closeCall("The agent ended the call."); }, 2500);
        }
        break;
    }
  }
}

// ---------------------------------------------------------------- call flow
function showScreen(name) {
  for (const id of ["idle", "incoming", "incall"]) $(id).hidden = id !== name;
}

// Back to the idle phone: no call, no open prompts, mic and voice stopped.
function closeCall(message) {
  if (!callId) return;
  callId = null;
  speech.stop();
  if (recognizer) recognizer.stop();
  $("approve-box").hidden = true;
  $("confirm-box").hidden = true;
  $("say-input").value = "";
  $("mic-msg").textContent = "";
  $("idle-msg").textContent = `${message} The case timeline and letters stay in the bank view. Press "Place help call" to call again.`;
  showScreen("idle");
  $("phone-status").textContent = "Call ended";
  $("phone").focus();
}

async function placeCall() {
  speech.stop();
  $("transcript").innerHTML = "";
  const r = await api("/api/calls", {});
  callId = r.call.id;
  renderCase(r.call);
  showScreen("incoming");
  $("phone-status").textContent = "Incoming call";
  $("answer").focus();
}

async function act(path, body, busyText) {
  if (!callId) return;
  const status = $("phone-status");
  const before = status.textContent;
  if (busyText) status.textContent = busyText;
  setBusy(true);
  try {
    const r = await api(`/api/calls/${callId}/${path}`, body ?? {});
    handle(r.events);
    renderCase(r.call);
  } catch (err) {
    addMsg("error", "Error", esc(err.message));
    status.textContent = before;
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  for (const id of ["approve", "yes", "no"]) $(id).disabled = busy;
  $("say-form").querySelector('button[type="submit"]').disabled = busy;
}

async function send(text) {
  $("say-input").value = "";
  await act("say", { text }, "Agent is thinking…");
}

$("answer").addEventListener("click", async () => {
  showScreen("incall");
  await act("answer");
  $("approve").focus();
});
$("decline").addEventListener("click", async () => { await act("decline"); closeCall("You declined the call. Nothing changed on your account."); });
$("approve").addEventListener("click", () => act("approve", {}, "Connecting the agent…"));
$("yes").addEventListener("click", () => act("decision", { send: true }, "Sending…"));
$("no").addEventListener("click", () => act("decision", { send: false }, "Agent is thinking…"));
$("hangup").addEventListener("click", async () => {
  speech.stop();
  await act("hangup");  // tell the server (cancels anything still waiting for a yes)
  closeCall("You hung up.");  // close the call screen even if the server didn't answer
});
$("mic").addEventListener("click", toggleMic);
$("voice-on").addEventListener("change", () => { if (!$("voice-on").checked) speech.stop(); });
$("voice-select").addEventListener("change", (e) => {
  speech.choose(e.target.value);
  speech.stop();
  speech.say("Hello Amira. This is how I sound.");
});
$("say-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("say-input").value.trim();
  if (!text) { $("mic-msg").textContent = "Type something first, or press Speak."; return; }
  $("mic-msg").textContent = "";
  send(text);
});
$("say-input").addEventListener("input", () => { $("mic-msg").textContent = ""; });

// ---------------------------------------------------------------- safeguards
$("phrase-reveal").addEventListener("click", async () => {
  phraseShown = !phraseShown;
  $("phrase-reveal").setAttribute("aria-pressed", String(phraseShown));
  $("phrase-reveal").textContent = phraseShown ? "Hide" : "Show";
  const r = await api("/api/enrolment");
  $("phrase-masked").textContent = phraseShown ? r.safe_phrase : r.masked;
});
$("phrase-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("phrase-msg");
  const value = $("phrase-input").value.trim();
  if (!value) { msg.className = "field-msg bad"; msg.textContent = "Enter a phrase first."; return; }
  try {
    const r = await api("/api/enrolment", { safe_phrase: value });
    $("phrase-masked").textContent = r.masked;
    phraseShown = false;
    $("phrase-reveal").textContent = "Show";
    $("phrase-input").value = "";
    msg.className = "field-msg good";
    msg.textContent = "Saved. The next call will open with it.";
  } catch (err) {
    msg.className = "field-msg bad";
    msg.textContent = err.message;
  }
});
$("phrase-input").addEventListener("input", () => { $("phrase-msg").textContent = ""; });
$("guard-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("guard-msg");
  const text = $("guard-input").value.trim();
  if (!text) { msg.className = "field-msg bad"; msg.textContent = "Enter a sentence first."; return; }
  const r = await api("/api/guard-check", { text });
  msg.className = "field-msg " + (r.allowed ? "good" : "bad");
  msg.textContent = r.allowed ? "Allowed: this would be spoken." : `Blocked (${r.reason}). Spoken instead: "${r.spoken}"`;
});
$("fw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await api("/api/firewall-check", { purpose: $("fw-purpose").value });
  $("fw-msg").className = "field-msg " + (r.allowed ? "good" : "bad");
  $("fw-msg").textContent = r.allowed ? "Allowed: used only to help the customer." : "Refused: " + r.message;
});

matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
  if (overview) renderForecast(lastCall ? lastCall.forecast : overview.forecast, lastCall ? lastCall.after : null);
});

loadOverview().catch((err) => { $("mode").textContent = "Could not load: " + err.message; });
