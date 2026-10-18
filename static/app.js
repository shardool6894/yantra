const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LETTERS = "ABCDEFGH";

const state = {
  manuals: [], preloaded: [], manualId: null,
  view: null,        // latest session view from the server
  trail: [],         // [{q, a}] questions answered so far
  doneSteps: [],     // step texts the operator has finished
  error: "", busy: false,
  report: null, sent: new Set(), noteDraft: "",
  ingesting: false, ingestResult: null, ingestError: "",
};

// ---------- api ----------
async function api(path, opts = {}) {
  const res = await fetch("/api" + path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.");
  return body;
}
const post = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data ?? {}) });

// ---------- tabs ----------
function route() {
  const tab = location.hash === "#manuals" ? "manuals" : "diagnose";
  $("#view-diagnose").hidden = tab !== "diagnose";
  $("#view-manuals").hidden = tab !== "manuals";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  if (tab === "manuals") loadManuals().then(renderManuals);
}

async function loadManuals() {
  try {
    const d = await api("/manuals");
    state.manuals = d.ingested;
    state.preloaded = d.preloaded;
    let saved = null;
    try { saved = Number(localStorage.getItem("yantra.manual")); } catch (e) { /* storage unavailable */ }
    if (!state.manuals.some((m) => m.id === state.manualId)) {
      state.manualId = state.manuals.some((m) => m.id === saved) ? saved : (state.manuals[0] || {}).id ?? null;
    }
  } catch (e) {
    state.error = e.message;
  }
}

// ---------- diagnose view ----------
const errorHtml = () => (state.error ? `<div class="error" role="alert">${esc(state.error)}</div>` : "");

function idleHtml() {
  if (!state.manuals.length) {
    return `<h1>Diagnose a fault</h1>
      <div class="card"><p>No machine manual is loaded yet.</p>
      <p class="muted">Load one on the <a href="#manuals">Manuals</a> page, then come back here.</p></div>`;
  }
  const options = state.manuals
    .map((m) => `<option value="${m.id}" ${m.id === state.manualId ? "selected" : ""}>${esc(m.machine)}</option>`).join("");
  const draft = esc(state.draft || "");
  return `<h1>What's wrong with the machine?</h1>
    <p class="lead">Describe the problem in your own words. You'll get a few quick questions, then step-by-step instructions.</p>
    <div class="card">
      <div class="field"><label for="machine">Machine</label><select id="machine">${options}</select></div>
      <div class="field"><label for="desc">Problem</label>
        <textarea id="desc" placeholder="For example: the ram is not moving, but I can hear the motor running">${draft}</textarea></div>
      <button class="primary" data-action="start">Find the fault</button>
    </div>`;
}

function contextHtml(v) {
  const trail = state.trail.length
    ? `<div class="card"><h3>So far</h3><ul class="trail">${state.trail
        .map((t) => `<li>${esc(t.q)}<span class="a">${esc(t.a)}</span></li>`).join("")}</ul></div>`
    : "";
  return `<div class="card"><h3>Problem</h3><p style="margin-bottom:${v.fault ? 10 : 0}px">${esc(v.description)}</p>
    ${v.fault ? `<span class="chip">${esc(v.fault.title)}</span>${pageRef(v.fault)}` : ""}</div>${trail}`;
}

const pageRef = (fault) => (fault && fault.source_pages ? ` <span class="muted pageref">Manual page ${esc(fault.source_pages)}</span>` : "");

const technicianLink = `<p style="text-align:center"><button class="link" data-action="escalate">Hand this to a technician</button></p>`;

function questionHtml(v) {
  const q = v.question;
  const count = q.kind === "fault" ? "Matching your problem" : `Question ${q.number}`;
  const opts = q.options
    .map((o, i) => `<button class="option" data-action="answer" data-id="${o.id}" data-label="${esc(o.label)}">
      <span class="letter">${LETTERS[i]}</span><span>${esc(o.label)}</span></button>`).join("");
  return `${contextHtml(v)}<div class="card"><div class="qcount">${count}</div>
    <p class="qtext">${esc(q.text)}</p><div class="options">${opts}</div></div>${technicianLink}`;
}

function fixingHtml(v) {
  const cause = `<div class="cause"><strong>Likely cause</strong>${esc(v.cause.description)}${v.fault && v.fault.source_pages ? `<div class="muted pageref">Manual page ${esc(v.fault.source_pages)}</div>` : ""}</div>`;
  const done = state.doneSteps.length
    ? `<ul class="done-steps">${state.doneSteps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : "";
  const total = v.step ? v.step.total : state.doneSteps.length;
  const bar = `<div class="progress" aria-hidden="true">${Array.from({ length: total }, (_, i) =>
    `<i class="${i < state.doneSteps.length ? "done" : i === state.doneSteps.length ? "now" : ""}"></i>`).join("")}</div>`;
  if (v.awaiting_result) {
    return `<div class="card">${cause}${bar}<p class="qtext">Did that fix the problem?</p>
      <div class="options"><button class="option primary" data-action="fixed" data-fixed="true">Yes, it's working</button>
      <button class="option" data-action="fixed" data-fixed="false">No, it's still not right</button></div>${done}</div>${technicianLink}`;
  }
  const s = v.step;
  const text = s.is_warning
    ? `<div class="step-warn">${esc(s.text)}</div>` : `<p class="step-text">${esc(s.text)}</p>`;
  return `<div class="card">${cause}<div class="qcount">Step ${s.number} of ${s.total}</div>${bar}${text}
    <button class="primary" data-action="done" style="width:100%;margin-top:${s.is_warning ? 18 : 0}px">Done</button>${done}</div>${technicianLink}`;
}

function endedHtml(v) {
  const again = `<button class="primary" data-action="reset">Start a new case</button>`;
  if (v.state === "resolved") {
    return `<div class="card outcome ok"><div class="badge">&#10003;</div><h2>Fixed</h2>
      <p>${esc(v.cause.description)}</p><p class="muted">Solved in ${Math.round(v.elapsed_s)} seconds.</p>${again}</div>`;
  }
  return `<div class="card outcome esc"><div class="badge">!</div><h2>Hand this to a technician</h2>
    <p>${esc(v.reason)}</p>${again}</div><div id="report-slot"></div>`;
}

function renderDiagnose() {
  const v = state.view;
  let html;
  if (!v) html = idleHtml();
  else if (v.state === "choose_fault" || v.state === "questioning") html = questionHtml(v);
  else if (v.state === "fixing") html = fixingHtml(v);
  else html = endedHtml(v);
  $("#view-diagnose").innerHTML = errorHtml() + html;
  if (v && v.state === "escalated") showReport(v.session_id);
}

// ---------- escalation report ----------
async function showReport(id) {
  const slot = $("#report-slot");
  if (!slot) return;
  try {
    if (!state.report || state.report.id !== id) state.report = { id, ...(await api(`/sessions/${id}/report`)) };
  } catch (e) {
    slot.innerHTML = `<div class="error">${esc(e.message)}</div>`;
    return;
  }
  const r = state.report.report;
  const row = (k, v) => (v ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : "");
  const list = (items, empty) => items.length
    ? `<ol class="plain">${items.map((t) => `<li>${t}</li>`).join("")}</ol>` : `<p class="muted">${empty}</p>`;
  slot.innerHTML = `<div class="card report"><h3>Report for the technician</h3>
    <dl>${row("Case", `${r.case_id}, opened ${r.opened}, ${r.time_on_case} on case`)}${row("Machine", r.machine)}
    ${row("Problem", r.symptom)}${row("Matched fault", r.fault || "No confident match")}${row("Manual page", r.source_pages)}${row("Likely cause", r.cause)}
    ${row("Why a technician", r.cause_note)}${row("Escalated because", r.reason)}</dl>
    <h3>Questions and answers</h3>${list(r.questions.map((q) => `${esc(q.question)}<span class="a">${esc(q.answer)}</span>`), "None were asked.")}
    <h3>Steps tried</h3>${list(r.steps_tried.map(esc), "None.")}
    ${r.failed_causes.length ? `<h3>Fixes that did not work</h3>${list(r.failed_causes.map(esc), "")}` : ""}
    <button data-action="copy-report">Copy report</button></div>${resolveHtml(id)}`;
}

function resolveHtml(id) {
  if (state.sent.has(id)) {
    return `<div class="card"><h3>Sent for review</h3><p style="margin:0">Thanks. A supervisor will check this fix and, if approved, it will be added so the next operator gets it.</p></div>`;
  }
  return `<div class="card"><h3>Was it fixed afterwards?</h3>
    <p class="muted">If the technician or you solved it, write down what fixed it. A supervisor reviews it before it is added.</p>
    <div class="field"><label for="note">What fixed it? One step per line.</label>
    <textarea id="note" placeholder="1. Replaced the blown fuse&#10;2. Restarted the press and ran three test cycles">${esc(state.noteDraft)}</textarea></div>
    <button class="primary" data-action="resolve">Send for review</button></div>`;
}

async function act(fn) {
  if (state.busy) return;
  state.busy = true;
  state.error = "";
  try { await fn(); } catch (e) { state.error = e.message; }
  state.busy = false;
  renderDiagnose();
}

const actions = {
  start: () => act(async () => {
    const desc = $("#desc").value;
    state.draft = desc;
    state.manualId = Number($("#machine").value);
    try { localStorage.setItem("yantra.manual", state.manualId); } catch (e) { /* ignore */ }
    state.view = await post("/sessions", { manual_id: state.manualId, description: desc });
    state.trail = []; state.doneSteps = [];
  }),
  answer: (btn) => act(async () => {
    const q = state.view.question;
    if (q.kind === "cause") state.trail.push({ q: q.text, a: btn.dataset.label });
    state.view = await post(`/sessions/${state.view.session_id}/answer`, { option_id: Number(btn.dataset.id) });
  }),
  done: () => act(async () => {
    const text = state.view.step.text;
    state.view = await post(`/sessions/${state.view.session_id}/step-done`);
    state.doneSteps.push(text);
  }),
  fixed: (btn) => act(async () => {
    state.view = await post(`/sessions/${state.view.session_id}/fix-result`, { fixed: btn.dataset.fixed === "true" });
    if (btn.dataset.fixed === "false") state.doneSteps = [];
  }),
  escalate: () => act(async () => {
    state.view = await post(`/sessions/${state.view.session_id}/escalate`);
  }),
  resolve: () => act(async () => {
    state.noteDraft = $("#note").value;
    await post(`/sessions/${state.view.session_id}/resolve`, { note: state.noteDraft });
    state.sent.add(state.view.session_id);
    state.noteDraft = "";
  }),
  "copy-report": async (btn) => {
    const text = state.report.text;
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {  // clipboard API blocked: fall back to a temporary textarea
      const t = document.createElement("textarea");
      t.value = text;
      document.body.appendChild(t);
      t.select();
      const ok = document.execCommand("copy");
      t.remove();
      if (!ok) { btn.textContent = "Copy failed, select the text instead"; return; }
    }
    btn.textContent = "Copied";
  },
  reset: () => { state.view = null; state.report = null; state.noteDraft = ""; state.trail = []; state.doneSteps = []; state.draft = ""; state.error = ""; renderDiagnose(); },
};

// ---------- manuals view ----------
function summaryHtml(r) {
  const warn = r.warnings.length ? `<ul class="muted">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "";
  return `<div class="card"><h3>Ingested: ${esc(r.machine)}</h3>
    <div class="summary"><div><b>${r.faults}</b><span>faults</span></div><div><b>${r.causes}</b><span>causes</span></div>
    <div><b>${r.fix_paths}</b><span>fix paths</span></div></div>
    <p class="muted" style="margin:0">${r.questions} questions and ${r.steps} steps.
    ${r.from_cache ? "Used the saved extraction, so no language model was called." : ""}</p>${warn}</div>`;
}

function renderManuals() {
  const list = state.manuals.length
    ? state.manuals.map((m) => `<div class="manual"><div><div class="name">${esc(m.machine)}</div>
        <div class="muted" style="font-size:14px">${esc(m.source_file)}</div></div>
        <div class="counts"><span><b>${m.faults}</b> faults</span><span><b>${m.causes}</b> causes</span><span><b>${m.fix_paths}</b> fix paths</span></div></div>`).join("")
    : `<p class="muted">Nothing ingested yet.</p>`;
  const pre = state.preloaded.length
    ? `<div class="card"><h3>Pre-loaded manuals</h3>${state.preloaded.map((f) => `<div class="manual"><span class="name">${esc(f)}</span>
        <button data-action="ingest-pre" data-file="${esc(f)}" ${state.ingesting ? "disabled" : ""}>Ingest</button></div>`).join("")}</div>` : "";
  const status = state.ingesting
    ? `<div class="card"><span class="spinner"></span>Reading the manual. A new PDF can take a few minutes.</div>`
    : state.ingestError ? `<div class="error" role="alert">${esc(state.ingestError)}</div>`
    : state.ingestResult ? summaryHtml(state.ingestResult) : "";
  $("#view-manuals").innerHTML = `<h1>Manuals</h1>
    <p class="lead">Ingestion reads a manual once and turns it into a fault graph. Diagnosis then only follows that graph.</p>
    ${status}<div class="card"><h3>Ready to use</h3>${list}</div>${pre}
    <div class="card"><h3>Upload a manual</h3>
      <div class="field"><label for="pdf">PDF file</label><input id="pdf" type="file" accept="application/pdf"></div>
      <button data-action="ingest-upload" ${state.ingesting ? "disabled" : ""}>Upload and ingest</button></div>`;
}

async function runIngest(form) {
  if (state.ingesting) return;
  state.ingesting = true; state.ingestResult = null; state.ingestError = "";
  renderManuals();
  try {
    state.ingestResult = await api("/ingest", { method: "POST", body: form });
  } catch (e) {
    state.ingestError = e.message;
  }
  state.ingesting = false;
  await loadManuals();
  renderManuals();
}

Object.assign(actions, {
  "ingest-pre": (btn) => { const f = new FormData(); f.append("preloaded", btn.dataset.file); runIngest(f); },
  "ingest-upload": () => {
    const file = $("#pdf").files[0];
    if (!file) { state.ingestError = "Choose a PDF file first."; renderManuals(); return; }
    const f = new FormData(); f.append("file", file); runIngest(f);
  },
});

// ---------- boot ----------
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (btn && actions[btn.dataset.action]) actions[btn.dataset.action](btn);
});
window.addEventListener("hashchange", route);
loadManuals().then(() => { route(); renderDiagnose(); });
