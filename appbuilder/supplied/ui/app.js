// SUPPLIED frontend. No framework, no build step. All text from the server is inserted with textContent, never innerHTML.
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, props = {}, ...kids) => { const e = document.createElement(tag); Object.assign(e, props); kids.flat().forEach(k => e.append(k)); return e; };
let CONFIG = {}, OWNER = null, CURRENT = null, STREAM = null;

function owner() {
  try { OWNER = localStorage.getItem("ownerToken"); } catch (e) {}
  if (!OWNER) {
    OWNER = Array.from(crypto.getRandomValues(new Uint8Array(24)), b => b.toString(16).padStart(2, "0")).join("");
    try { localStorage.setItem("ownerToken", OWNER); } catch (e) {}
  }
  return OWNER;
}
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { ...opts, headers: { "X-Owner-Token": owner(), ...(opts.json ? { "Content-Type": "application/json" } : {}) },
    body: opts.json ? JSON.stringify(opts.json) : opts.body });
  if (opts.raw) return r;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || "request failed (" + r.status + ")");
  return data;
}
function say(text) { const m = $("#msg"); m.hidden = !text; m.textContent = text || ""; }
async function guarded(fn, btn) {
  say(""); if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { say(e.message); } finally { if (btn) btn.disabled = false; }
}

async function init() {
  CONFIG = await (await fetch("/api/config")).json();
  const b = $("#banner");
  b.textContent = "";
  if (CONFIG.demo) b.append(el("b", { className: "demo", textContent: "DEMO MODE: scripted stand-in, not a model. " }));
  b.append(`provider ${CONFIG.provider} · model ${CONFIG.model || "none"} · ${CONFIG.implementation} loop · caps: ${CONFIG.limits.max_model_turns} steps, ${CONFIG.limits.max_repairs} repairs`);
  if (!CONFIG.demo && !CONFIG.live_model_configured) b.append(el("div", { textContent: "No live model is configured (see README). You can still open, export and import projects." }));
  await refreshList();
}
async function refreshList() {
  const list = await api("/projects"), ul = $("#projects"); ul.textContent = "";
  list.forEach(p => ul.append(el("li", { className: CURRENT && CURRENT.id === p.id ? "sel" : "", onclick: () => openProject(p.id) },
    el("span", { textContent: p.title }), el("span", { className: "badge s-" + p.status, textContent: p.status }))));
  if (!list.length) ul.append(el("li", { textContent: "No projects yet", style: "cursor:default" }));
}
async function openProject(id) {
  CURRENT = await api("/projects/" + id); render(); refreshList();
}

function section(title, ...kids) { return el("div", { className: "card" }, el("h2", { textContent: title }), ...kids); }
function pre(t) { return el("pre", { textContent: t }); }
function button(label, fn, cls = "") { const b = el("button", { className: "btn " + cls, textContent: label }); b.onclick = () => guarded(fn, b); return b; }

function render() {
  const p = CURRENT, root = $("#project"); root.hidden = !p; root.textContent = ""; if (!p) return;
  const st = p.status, head = el("div", { className: "card" },
    el("div", { className: "row" }, el("h2", { textContent: p.title }), el("span", { className: "badge s-" + st.status, textContent: st.status })));
  if (st.status === "verified") head.append(el("p", { textContent: `Verified: ${p.approved ? "this app " : ""}${st.wording} (${st.checks.passed}/${st.checks.total} checks; ${st.conditions.browser || "browser"}, clock ${st.conditions.fixed_time || "real"}, clean storage per check).` }));
  else if (st.status === "unresolved_requirements") head.append(el("p", { className: "proto", textContent: `NOT complete and NOT verified: ${st.reason}. ${st.partial_draft ? "The files below are a partial draft." : ""} Choose a supported replacement or remove each one from scope.` }));
  else if (st.status === "incomplete" || st.status === "failed_checks") head.append(el("p", { textContent: "NOT verified. See the failure report below." }));
  root.append(head);
  const pend = p.pending;
  if (pend) root.append(pendingCard(p, pend));
  const lb = p.last_build;
  if (lb && lb.failure) root.append(failureCard(lb.failure));
  if (p.files.length) root.append(previewCard(p));
  if (p.approved) root.append(approvedCard(p), revisionCard(p));
  root.append(usageCard(p), traceCard(p), actionsCard(p));
}
function pendingCard(p, pend) {
  const c = section(({ scope: "Review the proposed scope", criteria: "Review the acceptance criteria", revision: "Review the changed requirements and criteria",
    unsupported: "This request is not supported", clarification: "A clarification is needed", revision_unsupported: "This change is not supported" })[pend.kind] || "Pending");
  c.append(pre(pend.readable || pend.question || ""));
  const fb = el("input", { type: "text", placeholder: "Ask for a change to this proposal (optional)" });
  let approve = { scope: ["approve-scope", pend.scope_hash], criteria: ["approve-criteria", pend.criteria_hash], revision: ["approve-revision", pend.criteria_hash] }[pend.kind];
  if (approve && (pend.unverifiable || []).length) {
    // no "approve anyway": each unresolved requirement needs the user's decision first
    approve = null;
    pend.unverifiable.forEach(u => c.append(unresolvedItem(p, u)));
  }
  if (approve) {
    c.append(pre(pend.kind === "scope" ? "Approving lets the agent translate these requirements into checks. Nothing is built yet." :
      "Approving makes these checks the ONLY definition of 'verified'. The agent cannot edit them. Approval then starts the " + (p.approved ? "revision" : "build") + "."));
    c.append(button("Approve", async () => { watch(p.id); CURRENT = await api(`/projects/${p.id}/${approve[0]}`, { method: "POST", json: { hash: approve[1] } }); render(); refreshList(); }));
    c.append(el("div", {}, fb), button("Send change request", async () => { watch(p.id); CURRENT = await api(`/projects/${p.id}/revise-proposal`, { method: "POST", json: { feedback: fb.value } }); render(); }, "secondary"));
  }
  if (pend.kind === "unsupported" && p.pending.scope.proposed_alternative)
    c.append(button("Use the suggested alternative", async () => { CURRENT = await api(`/projects/${p.id}/accept-alternative`, { method: "POST" }); render(); }));
  if (pend.kind === "clarification") {
    c.append(button("Replace the old requirement with this change", async () => { watch(p.id); CURRENT = await api(`/projects/${p.id}/resolve`, { method: "POST", json: { choice: "replace" } }); render(); }));
    c.append(button("Keep the approved requirements (drop the change)", async () => { CURRENT = await api(`/projects/${p.id}/resolve`, { method: "POST", json: { choice: "keep" } }); render(); }, "secondary"));
  }
  return c;
}
function unresolvedItem(p, u) {
  const box = el("div", { className: "unres" });
  const ta = el("textarea", { rows: 2, maxLength: 500, value: u.proposed_alternative || "", placeholder: "Write a requirement that a browser check can verify" });
  box.append(el("b", { textContent: `${u.requirement_id} cannot be checked` }), el("p", { textContent: (u.text ? u.text + " — " : "") + u.reason }),
    el("label", { textContent: "Supported replacement (you decide the wording):" }), ta,
    button("Use this replacement", async () => { watch(p.id); CURRENT = await api(`/projects/${p.id}/resolve-item`, { method: "POST", json: { requirement_id: u.requirement_id, action: "replace", replacement: ta.value } }); render(); refreshList(); }),
    button("Remove from scope", async () => { CURRENT = await api(`/projects/${p.id}/resolve-item`, { method: "POST", json: { requirement_id: u.requirement_id, action: "remove" } }); render(); refreshList(); }, "secondary"));
  return box;
}
function failureCard(f) {
  const c = section("Failure report"); c.append(el("p", { textContent: f.headline }));
  (f.failing_checks || []).forEach(x => c.append(el("div", { textContent: `${x.id} ${x.title} [${x.status}]: ${x.message}` })));
  if (f.tried) c.append(pre(`Tried: ${f.tried.model_turns} model turns, ${f.tried.repairs} repairs (limits ${f.limits.max_model_turns} / ${f.limits.max_repairs}), ${f.tried.diagnostics} diagnostics, ${f.tried.file_writes} file writes.`));
  if (f.status_note) c.append(el("p", { textContent: f.status_note }));
  if (f.provider_message) c.append(pre(f.provider_message));
  return c;
}
function previewCard(p) {
  const c = section("Preview (separate origin, network blocked)");
  const first = p.files.includes("index.html") ? "index.html" : p.files.find(f => f.endsWith(".html"));
  if (first) c.append(el("iframe", { src: `/preview/${p.id}/${first}`, title: "App preview", sandbox: "allow-scripts allow-forms" }));
  c.append(el("p", { className: "hint", textContent: "Files: " + p.files.join(", ") + ". The preview is a convenience; only the trusted checks decide 'verified'." }));
  return c;
}
function approvedCard(p) {
  const a = p.approved, d = el("details", { className: "card" }, el("summary", { textContent: `Approved requirements and criteria (version ${a.approval.version})` }));
  d.append(pre(a.scope_text), pre(a.readable));
  (a.resolutions || []).forEach(r => d.append(pre(`Your decision: ${r.requirement_id} ${r.action === "remove" ? "removed from scope" : "replaced"} (was: ${r.original_text})` + (r.replacement_text ? `\nNow: ${r.replacement_text}` : ""))));
  return d;
}
function revisionCard(p) {
  const c = section("Request a change"), t = el("input", { type: "text", placeholder: "e.g. Rename the add button to 'Save expense'" });
  c.append(t, button("Request change", async () => { watch(p.id); CURRENT = await api(`/projects/${p.id}/revise`, { method: "POST", json: { request: t.value } }); render(); refreshList(); }));
  if (p.restore_point) c.append(button("Restore last working version", async () => { CURRENT = await api(`/projects/${p.id}/restore`, { method: "POST" }); render(); refreshList(); }, "secondary"),
    el("p", { className: "hint", textContent: `Last working version saved ${p.restore_point.saved_at} (only one is kept).` }));
  if (["incomplete", "unverified", "failed_checks"].includes(p.status.status) && !p.pending)
    c.append(button("Try the build again", async () => { watch(p.id); CURRENT = await api(`/projects/${p.id}/rebuild`, { method: "POST" }); render(); }, "secondary"));
  return c;
}
function usageCard(p) {
  const u = p.usage, c = section("Model usage and cost");
  const cost = u.cost.amount == null ? "unpriced (" + u.cost.note + ")" : `${u.cost.amount} (${u.cost.note})`;
  c.append(pre(`calls ${u.model_calls} (${u.calls_without_usage} without reported usage) · prompt tokens ${u.prompt_tokens} · completion tokens ${u.completion_tokens}\nmodels: ${u.models.join(", ") || "none"}\ncost: ${cost}`));
  return c;
}
function traceCard(p) {
  const d = el("details", { className: "card", id: "traceCard" }, el("summary", { textContent: "Live trace" }), el("div", { className: "stream", id: "stream" }), el("div", { className: "log", id: "log" }));
  d.open = true; return d;
}
function actionsCard(p) {
  const c = el("div", { className: "card row" });
  c.append(button("Export (.zip)", async () => { const r = await api(`/projects/${p.id}/export`, { raw: true }); if (!r.ok) throw new Error("export failed");
    const a = el("a", { href: URL.createObjectURL(await r.blob()), download: p.id + ".zip" }); a.click(); }, "secondary"));
  c.append(button("Delete project", async () => { if (!confirm("Delete this project from this browser's saved list and the server?")) return;
    await api("/projects/" + p.id, { method: "DELETE" }); CURRENT = null; render(); refreshList(); }, "danger"));
  return c;
}

// ---- live events (SSE over fetch so the owner header can be sent)
async function watch(id) {
  if (STREAM) STREAM.abort();
  STREAM = new AbortController();
  try {
    const r = await fetch(`/api/projects/${id}/events`, { headers: { "X-Owner-Token": owner() }, signal: STREAM.signal });
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n\n")) >= 0) { const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6))); }
    }
  } catch (e) { /* aborted or closed */ }
}
function onEvent(ev) {
  const log = $("#log"), s = $("#stream"); if (!log) return;
  if (ev.type === "model_text") { s.textContent = (s.textContent + ev.delta).slice(-600); return; }
  const line = { check_report: x => `${x.kind} check: ${x.all_passed ? "ALL PASSED" : "failed " + (x.failed || []).join(",")}`,
    tool_call: x => `tool ${x.name}${x.ok === false ? " (refused: " + x.error + ")" : ""}`, repair_started: x => `repair ${x.repair} of ${x.of}`,
    model_call_end: x => `model ${x.kind} ${x.latency_ms}ms ${x.usage ? x.usage.prompt_tokens + "+" + x.usage.completion_tokens + " tokens" : "usage not reported"}`,
    transport_retry: x => `retry ${x.attempt} (${x.kind}) in ${x.wait_s}s`, build_end: x => `STOP: ${x.stop_reason} -> ${x.status}`,
    auto_check: x => "model stopped without a current check; running the trusted checks" }[ev.type];
  if (line) { log.prepend(el("div", { textContent: line(ev) })); }
}

$("#newForm").onsubmit = e => { e.preventDefault(); const b = $("#createBtn");
  guarded(async () => { const text = $("#request").value; CURRENT = null; const tmp = await api("/projects", { method: "POST", json: { request: text } }); CURRENT = tmp; $("#request").value = ""; render(); refreshList(); }, b); };
$("#importFile").onchange = async e => { const f = e.target.files[0]; if (!f) return;
  await guarded(async () => { CURRENT = await api("/import", { method: "POST", body: await f.arrayBuffer() }); render(); refreshList(); }); e.target.value = ""; };
init().catch(e => say(e.message));
