const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { pending: [], reviewed: [], notice: "", error: "", busy: false };

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.");
  return body;
}

async function load() {
  try {
    [state.pending, state.reviewed] = await Promise.all([api("/reviews?status=pending"), api("/reviews?status=reviewed")]);
  } catch (e) {
    state.error = e.message;
  }
  render();
}

function list(items) {
  return items.length ? `<ol class="plain">${items.join("")}</ol>` : "";
}

function caseHtml(r) {
  const qa = r.questions.map((q) => `<li>${esc(q.question)}<span class="a">${esc(q.answer)}</span></li>`);
  const tried = r.steps_tried.map((t) => `<li>${esc(t)}</li>`);
  return `<div class="card report">
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <span class="muted">Case ${r.session_id} &middot; ${esc(r.machine)}</span>
      ${r.fault ? `<span class="chip">${esc(r.fault)}</span>` : `<span class="chip">No match</span>`}
    </div>
    <dl><dt>Problem</dt><dd>${esc(r.description)}</dd><dt>Escalated because</dt><dd>${esc(r.reason)}</dd></dl>
    ${qa.length ? `<h3>Questions and answers</h3>${list(qa)}` : ""}
    ${tried.length ? `<h3>Steps tried</h3>${list(tried)}` : ""}
    <h3>How it was fixed</h3><p class="note">${esc(r.resolution_note)}</p>
    <div class="cause"><strong>If you approve</strong>${esc(r.plan.summary)}</div>
    <div class="row">
      <button class="primary" data-action="approve" data-id="${r.id}">Approve</button>
      <button data-action="reject" data-id="${r.id}">Reject</button>
    </div></div>`;
}

function reviewedHtml() {
  if (!state.reviewed.length) return "";
  const rows = state.reviewed.slice(0, 10).map((r) => `<div class="manual"><div><div class="name">${esc(r.description)}</div>
    <div class="muted" style="font-size:14px">${esc(r.plan.summary)}</div></div>
    <span class="chip ${r.status}">${r.status === "approved" ? "Approved" : "Rejected"}</span></div>`).join("");
  return `<div class="card"><h3>Recently reviewed</h3>${rows}</div>`;
}

function render() {
  const n = state.pending.length;
  $("#main").innerHTML = `<h1>Review queue</h1>
    <p class="lead">Cases that were escalated and then fixed. Approve one to add it to the fault graph, so the next operator gets the answer straight away.</p>
    ${state.error ? `<div class="error" role="alert">${esc(state.error)}</div>` : ""}
    ${state.notice ? `<div class="card" style="background:var(--ok-soft);color:var(--ok)">${esc(state.notice)}</div>` : ""}
    ${n ? state.pending.map(caseHtml).join("") : `<div class="card"><p class="muted" style="margin:0">Nothing waiting for review.</p></div>`}
    ${reviewedHtml()}`;
}

async function decide(id, verb) {
  if (state.busy) return;
  state.busy = true; state.error = ""; state.notice = "";
  try {
    await api(`/reviews/${id}/${verb}`, { method: "POST" });
    state.notice = verb === "approve" ? "Approved and added to the fault graph." : "Rejected. The graph was not changed.";
  } catch (e) {
    state.error = e.message;
  }
  state.busy = false;
  await load();
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (btn) decide(Number(btn.dataset.id), btn.dataset.action);
});

load();
