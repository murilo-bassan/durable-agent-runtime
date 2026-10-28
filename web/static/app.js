// runloop — operator console for the durable agent runtime. Vanilla JS, no build.

const root = document.getElementById("root");
const state = { selected: null, owner: "ana", runs: [], approvals: [], metrics: null, steps: [], run: null };

// ------------------------------------------------------------------ i18n (EN source strings are the keys)
const PT = {
 "Queue depth": "Tamanho da fila",
 "Running": "Rodando",
 "Awaiting human": "Aguardando humano",
 "p95 latency": "latência p95",
 "Tokens · 24h": "Tokens · 24h",
 "Item": "Item",
 "Quantity": "Quantidade",
 "Max unit price": "Preço unitário máximo",
 "Start agent run ▸": "Iniciar execução do agente ▸",
 "New procurement run": "Nova execução de compra",
 "idempotent": "idempotente",
 "Runs": "Execuções",
 "No runs yet.": "Nenhuma execução ainda.",
 "Approval inbox": "Caixa de aprovações",
 "Approve": "Aprovar",
 "Reject": "Rejeitar",
 "Select a run to see its step timeline.": "Selecione uma execução para ver a linha do tempo.",
 "cancel": "cancelar",
 "waiting for a worker…": "aguardando um worker…",
 "owner": "dono",
 "API docs": "docs da API",
 "durable agent runtime": "runtime durável de agentes",
 "model → final answer": "modelo → resposta final",
 "approved — run requeued": "aprovado — execução voltou para a fila",
 "rejected — agent will adapt": "rejeitado — o agente vai se adaptar",
 "queued": "na fila",
 "running": "rodando",
 "waiting approval": "aguardando aprovação",
 "succeeded": "concluído",
 "failed": "falhou",
 "cancelled": "cancelado",
 "dead": "morto",
 "cancel requested": "cancelamento solicitado",
 "paused for approval": "pausado para aprovação",
 "attempt": "tentativa",
 "attempts": "tentativas"
};
let lang = (() => { try { return localStorage.getItem("lang"); } catch { return null; } })()
  || ((navigator.language || "").toLowerCase().startsWith("pt") ? "pt" : "en");
const t = (s) => (lang === "pt" && PT[s]) || s;
function setLang(l) { lang = l; try { localStorage.setItem("lang", l); } catch {} document.documentElement.lang = l === "pt" ? "pt-BR" : "en"; formCard = typeof formCard === "undefined" ? undefined : null; render(); }
function langToggle() {
  return h("span", { class: "lang" }, ["en", "pt"].map((l) => h("button", { class: lang === l ? "on" : "", onclick: () => setLang(l) }, l.toUpperCase())));
}


function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json", "X-Owner": state.owner, ...headers },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)), { status: res.status });
  return data;
}

function toast(msg, bad = false) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3000);
}

const STATUS = {
  queued: "chip", running: "chip ok pulse", waiting_approval: "chip warn", succeeded: "chip ok",
  failed: "chip bad", cancelled: "chip", dead: "chip bad",
};
const since = (iso) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 60 ? `${s.toFixed(0)}s ago` : s < 3600 ? `${(s / 60).toFixed(0)}m ago` : `${(s / 3600).toFixed(1)}h ago`;
};
const short = (id) => id.slice(0, 8);

// ------------------------------------------------------------------ data

async function refresh() {
  const [runs, approvals, metrics] = await Promise.all([api("/v1/runs?limit=40"), api("/v1/approvals"), api("/v1/metrics")]);
  Object.assign(state, { runs, approvals, metrics });
  if (state.selected) {
    [state.run, state.steps] = await Promise.all([api(`/v1/runs/${state.selected}`), api(`/v1/runs/${state.selected}/steps`)]);
  }
  render();
}

async function submit(form) {
  const body = {
    agent: "procurement",
    input: { item: form.item.value, quantity: Number(form.quantity.value), max_unit_price: Number(form.max.value) },
  };
  try {
    const run = await api("/v1/runs", { method: "POST", body, headers: { "Idempotency-Key": crypto.randomUUID() } });
    state.selected = run.id;
    toast(`run ${short(run.id)} queued`);
    refresh();
  } catch (e) { toast(e.message, true); }
}

async function decide(a, approve) {
  try {
    await api(`/v1/approvals/${a.id}`, { method: "POST", body: { approve, decided_by: "operator", note: approve ? "" : "rejected in console" } });
    toast(approve ? "approved — run requeued" : t("rejected — agent will adapt"));
    refresh();
  } catch (e) { toast(e.message, true); }
}

async function cancel(id) {
  const r = await api(`/v1/runs/${id}/cancel`, { method: "POST" });
  toast(t(r.status.replace("_", " ")));
  refresh();
}

// ------------------------------------------------------------------ views

function kpis() {
  const m = state.metrics || { by_status: {} };
  const cell = (label, v, unit, cls = "") => h("div", { class: "kpi" }, h("div", { class: "label" }, label),
    h("div", { class: "v " + cls }, v ?? "—", unit ? h("small", {}, unit) : null));
  return h("div", { class: "grid kpis", style: "grid-template-columns:repeat(5,minmax(0,1fr))" },
    cell("Queue depth", m.queue_depth, m.oldest_queued_s ? `oldest ${m.oldest_queued_s.toFixed(0)}s` : ""),
    cell("Running", m.by_status.running || 0),
    cell("Awaiting human", m.pending_approvals, "", m.pending_approvals ? "accent" : ""),
    cell("p95 latency", m.latency_p95_s ? m.latency_p95_s.toFixed(1) : "—", "s"),
    cell("Tokens · 24h", m.tokens_24h ? new Intl.NumberFormat().format(m.tokens_24h) : 0));
}

let formCard = null; // built once so polling re-renders never wipe what the user is typing
function newRunForm() {
  if (formCard) return formCard;
  const form = h("form", { class: "body stack", onsubmit: (e) => { e.preventDefault(); submit(form); } },
    h("div", { class: "field" }, h("span", { class: "label" }, t("Item")),
      h("select", { name: "item" }, ["laptops", "monitors", "office chairs", "desks", "keyboards", "headsets"].map((i) => h("option", {}, i)))),
    h("div", { class: "grid two", style: "gap:10px" },
      h("div", { class: "field" }, h("span", { class: "label" }, t("Quantity")), h("input", { class: "input", name: "quantity", type: "number", value: 10, min: 1 })),
      h("div", { class: "field" }, h("span", { class: "label" }, t("Max unit price")), h("input", { class: "input", name: "max", type: "number", value: 5000, min: 1 }))),
    h("button", { class: "btn primary", type: "submit" }, t("Start agent run ▸")));
  formCard = h("section", { class: "card" }, h("header", {}, h("span", { class: "label" }, t("New procurement run")), h("span", { class: "chip violet" }, t("idempotent"))), form);
  return formCard;
}

function runList() {
  return h("section", { class: "card" }, h("header", {}, h("span", { class: "label" }, t("Runs")), h("span", { class: "muted mono" }, state.runs.length)),
    state.runs.length ? state.runs.map((r) => h("div", {
      class: "run-row" + (r.id === state.selected ? " active" : ""),
      onclick: () => { state.selected = r.id; refresh(); },
    },
      h("span", { class: "mono" }, `${r.input.quantity}× ${r.input.item}`),
      h("span", { class: STATUS[r.status] }, t(r.status.replace("_", " "))),
      h("span", { class: "hash" }, `${short(r.id)} · ${t("attempt")} ${r.attempts} · ${since(r.created_at)}`),
      h("span", { class: "hash" }, r.tokens_in + r.tokens_out ? `${r.tokens_in + r.tokens_out} tok` : ""))) : h("div", { class: "empty" }, t("No runs yet.")));
}

function approvalsPanel() {
  if (!state.approvals.length) return null;
  return h("section", { class: "card" }, h("header", {}, h("span", { class: "label accent" }, t("Approval inbox")), h("span", { class: "chip warn" }, state.approvals.length)),
    h("div", { class: "body stack" }, state.approvals.map((a) => h("div", { class: "approval-card" },
      h("div", { class: "row", style: "justify-content:space-between" }, h("b", { class: "mono" }, a.tool), h("span", { class: "hash" }, `run ${short(a.run_id)}`)),
      h("div", { class: "mono" }, `${a.arguments.quantity}× ${a.arguments.item} from ${a.arguments.supplier_id} @ ${a.arguments.unit_price.toFixed(2)}`,
        h("span", { class: "muted" }, ` = ${(a.arguments.quantity * a.arguments.unit_price).toFixed(2)}`)),
      h("div", { class: "hash" }, `bound to sha256 ${a.action_sha.slice(0, 16)}… · expires ${new Date(a.expires_at).toLocaleTimeString()}`),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => decide(a, true) }, t("Approve")),
        h("button", { class: "btn danger", onclick: () => decide(a, false) }, t("Reject")))))));
}

function stepItem(s) {
  const out = s.output || {};
  const node = { llm: "AI", tool: "fn", approval: "!", system: "·" }[s.kind];
  let title, body;
  if (s.kind === "llm") {
    const calls = out.tool_calls || [];
    title = calls.length ? `model → ${calls.map((c) => c.name).join(", ")}` : t("model → final answer");
    body = calls.length ? calls.map((c) => `${c.name}(${JSON.stringify(c.arguments)})`).join("\n") : out.content;
  } else if (s.kind === "tool") {
    title = s.name;
    body = JSON.stringify(out).slice(0, 260);
  } else if (s.kind === "approval") {
    title = `${t("paused for approval")} · ${s.name}`;
    body = JSON.stringify(s.input);
  } else { title = s.name; body = JSON.stringify(out); }
  return h("div", { class: `tl-item ${s.kind} ${s.status === "error" ? "error" : ""}` },
    h("div", { class: "tl-node" }, node),
    h("div", {},
      h("div", { class: "tl-head" }, h("b", { class: "mono" }, title),
        s.status !== "ok" ? h("span", { class: s.status === "error" ? "chip bad" : "chip warn" }, s.status) : null,
        h("span", { class: "hash" }, `#${s.n} · ${t("attempt")} ${s.attempt}${s.duration_ms != null ? ` · ${s.duration_ms}ms` : ""}`)),
      h("div", { class: "tl-body" }, body)));
}

function detail() {
  const r = state.run;
  if (!r) return h("section", { class: "card empty" }, t("Select a run to see its step timeline."));
  const live = !["succeeded", t("failed"), t("cancelled"), "dead"].includes(r.status);
  return h("section", { class: "card" },
    h("header", {},
      h("div", {}, h("div", { class: "label" }, `run ${r.id}`), h("div", { class: "mono" }, `${r.agent} · prompt ${r.prompt_name} v${r.prompt_version} · ${r.model}`)),
      h("div", { class: "row" }, h("span", { class: STATUS[r.status] }, h("i", { class: "dot" }), t(r.status.replace("_", " "))),
        live ? h("button", { class: "btn danger", onclick: () => cancel(r.id) }, t("cancel")) : null)),
    h("div", { class: "body stack" },
      r.result?.answer ? h("div", { class: "answer" }, r.result.answer) : null,
      r.error ? h("div", { class: "approval-card", style: "border-color:rgba(255,77,94,.4);background:var(--danger-dim)" }, h("span", { class: "mono" }, JSON.stringify(r.error))) : null,
      h("div", { class: "meta-row" },
        h("span", { class: "chip" }, `${t("attempts")} ${r.attempts}`), h("span", { class: "chip" }, `${r.tokens_in} in / ${r.tokens_out} out`),
        r.worker_id ? h("span", { class: "chip violet" }, r.worker_id) : null),
      state.steps.length ? h("div", { class: "timeline" }, state.steps.map(stepItem)) : h("div", { class: "muted" }, t("waiting for a worker…"))));
}

function render() {
  root.replaceChildren(h("div", { class: "main" },
    h("div", { class: "topbar" },
      h("div", { class: "row", style: "gap:14px" },
        h("div", { class: "brand-mark" }, h("i"), h("i"), h("i"), h("i")),
        h("div", {}, h("div", { class: "label" }, t("durable agent runtime")), h("h1", {}, "runloop"))),
      h("div", { class: "row" }, langToggle(), h("span", { class: "label" }, t("owner")), h("span", { class: "chip ok" }, state.owner),
        h("a", { class: "btn ghost", href: "/docs", target: "_blank" }, t("API docs")))),
    h("div", { class: "content" }, kpis(),
      h("div", { class: "grid split", style: "grid-template-columns:380px minmax(0,1fr)" },
        h("div", { class: "stack", style: "gap:20px" }, newRunForm(), approvalsPanel(), runList()),
        detail()))));
}

refresh();
setInterval(() => refresh().catch(() => {}), 1500);
