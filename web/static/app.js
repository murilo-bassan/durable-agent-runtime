// runloop — operator console for the durable agent runtime. Vanilla JS, no build.

const root = document.getElementById("root");
const OWNERS = ["ana", "bruno", "carla"];
const state = { selected: null, owner: "ana", budget: null, runs: [], approvals: [], metrics: null, steps: [], run: null };

// ------------------------------------------------------------------ i18n (EN source strings are the keys)
const PT = {
 "Purchasing agent": "Agente de compras",
 "Ask the agent to buy something. It compares suppliers, then waits for your OK before placing the order. If the server crashes halfway, it picks up where it stopped and never places the same order twice.":
   "Peça ao agente para comprar algo. Ele compara fornecedores e espera o seu OK antes de fazer o pedido. Se o servidor cair no meio, ele continua de onde parou e nunca faz o mesmo pedido duas vezes.",
 "Buying as": "Comprando como",
 "tokens left today": "tokens restantes hoje",
 "AI usage this user has left in the last 24 hours": "uso de IA que este usuário ainda tem nas últimas 24 horas",
 "In line": "Na fila",
 "Working now": "Trabalhando agora",
 "Waiting for your OK": "Esperando seu OK",
 "Done": "Concluídos",
 "AI usage today": "Uso de IA hoje",
 "tokens": "tokens",
 "What should the agent buy?": "O que o agente deve comprar?",
 "Item": "Item",
 "Quantity": "Quantidade",
 "Max price per unit": "Preço máximo por unidade",
 "Start purchase": "Iniciar compra",
 "Clicking twice won't create two requests.": "Clicar duas vezes não cria dois pedidos.",
 "Your requests": "Seus pedidos",
 "No requests yet. Start one above.": "Nenhum pedido ainda. Comece um acima.",
 "The agent wants to place an order": "O agente quer fazer um pedido",
 "each": "cada",
 "total": "total",
 "This OK only covers these exact values. Expires at": "Este OK vale só para estes valores exatos. Expira às",
 "Approve order": "Aprovar pedido",
 "Reject": "Rejeitar",
 "Pick a request to see what the agent did, step by step.": "Escolha um pedido para ver o que o agente fez, passo a passo.",
 "Cancel": "Cancelar",
 "Waiting for a free worker…": "Esperando um worker livre…",
 "Request": "Pedido",
 "AI usage": "Uso de IA",
 "try": "tentativa",
 "Steps": "Passos",
 "Technical details": "Detalhes técnicos",
 "Approved. The agent is back to work.": "Aprovado. O agente voltou ao trabalho.",
 "Rejected. The agent will look for another option.": "Rejeitado. O agente vai procurar outra opção.",
 "Request created": "Pedido criado",
 "queued": "na fila",
 "running": "trabalhando",
 "waiting approval": "precisa do seu OK",
 "succeeded": "concluído",
 "failed": "falhou",
 "cancelled": "cancelado",
 "dead": "desistiu",
 "cancel requested": "cancelamento pedido",
 "The AI decided to": "A IA decidiu",
 "The AI wrote the summary": "A IA escreveu o resumo",
 "Search suppliers": "Buscar fornecedores",
 "Get a quote": "Pedir orçamento",
 "Place the order": "Fazer o pedido",
 "Paused, waiting for your OK": "Pausado, esperando seu OK",
 "Out of AI budget": "Sem orçamento de IA",
 "Found": "Encontrou",
 "suppliers": "fornecedores",
 "does not sell this item": "não vende este item",
 "per unit": "por unidade",
 "Order": "Pedido",
 "created": "criado",
 "You rejected it, so nothing was ordered.": "Você rejeitou, então nada foi pedido.",
 "laptops": "notebooks", "monitors": "monitores", "office chairs": "cadeiras de escritório", "desks": "mesas",
 "keyboards": "teclados", "headsets": "headsets"
};
let lang = (() => { try { return localStorage.getItem("lang"); } catch { return null; } })()
  || ((navigator.language || "").toLowerCase().startsWith("pt") ? "pt" : "en");
const t = (s) => (lang === "pt" && PT[s]) || s;
function setLang(l) { lang = l; try { localStorage.setItem("lang", l); } catch {} document.documentElement.lang = l === "pt" ? "pt-BR" : "en"; formCard = null; render(); }
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
const statusChip = (status) => h("span", { class: STATUS[status] }, h("i", { class: "dot" }), t(status.replace("_", " ")));
const since = (iso) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const v = s < 60 ? `${s.toFixed(0)}s` : s < 3600 ? `${(s / 60).toFixed(0)} min` : `${(s / 3600).toFixed(1)} h`;
  return lang === "pt" ? `há ${v}` : `${v} ago`;
};
const short = (id) => id.slice(0, 8);
const num = (n, digits = 0) => new Intl.NumberFormat(lang === "pt" ? "pt-BR" : "en-US",
  { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
const money = (n) => num(n, 2);
const TOOL_TITLES = { search_suppliers: "Search suppliers", get_quote: "Get a quote", create_purchase_order: "Place the order" };
const toolTitle = (name) => t(TOOL_TITLES[name] || name);

// ------------------------------------------------------------------ data

async function refresh() {
  const [runs, approvals, metrics, budget] = await Promise.all([api("/v1/runs?limit=40"), api("/v1/approvals"), api("/v1/metrics"),
    api(`/v1/budget/${state.owner}`)]);
  Object.assign(state, { runs, approvals, metrics, budget: budget.remaining_tokens_24h });
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
    toast(t("Request created"));
    refresh();
  } catch (e) { toast(e.message, true); }
}

async function decide(a, approve) {
  try {
    await api(`/v1/approvals/${a.id}`, { method: "POST", body: { approve, decided_by: "operator", note: approve ? "" : "rejected in console" } });
    toast(approve ? t("Approved. The agent is back to work.") : t("Rejected. The agent will look for another option."));
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
  const cell = (label, v, unit, cls = "") => h("div", { class: "kpi" }, h("div", { class: "label" }, t(label)),
    h("div", { class: "v " + cls }, v ?? "—", unit ? h("small", {}, unit) : null));
  return h("div", { class: "grid kpis", style: "grid-template-columns:repeat(5,minmax(0,1fr))" },
    cell("In line", m.queue_depth),
    cell("Working now", m.by_status.running || 0),
    cell("Waiting for your OK", m.pending_approvals, "", m.pending_approvals ? "accent" : ""),
    cell("Done", m.by_status.succeeded || 0),
    cell("AI usage today", m.tokens_24h ? num(m.tokens_24h) : 0, t("tokens")));
}

let formCard = null; // built once so polling re-renders never wipe what the user is typing
function newRunForm() {
  if (formCard) return formCard;
  const form = h("form", { class: "body stack", onsubmit: (e) => { e.preventDefault(); submit(form); } },
    h("div", { class: "field" }, h("span", { class: "label" }, t("Item")),
      h("select", { name: "item" }, ["laptops", "monitors", "office chairs", "desks", "keyboards", "headsets"].map((i) => h("option", { value: i }, t(i))))),
    h("div", { class: "grid two", style: "gap:10px" },
      h("div", { class: "field" }, h("span", { class: "label" }, t("Quantity")), h("input", { class: "input", name: "quantity", type: "number", value: 10, min: 1 })),
      h("div", { class: "field" }, h("span", { class: "label" }, t("Max price per unit")), h("input", { class: "input", name: "max", type: "number", value: 5000, min: 1 }))),
    h("button", { class: "btn primary", type: "submit" }, t("Start purchase")),
    h("div", { class: "label" }, t("Clicking twice won't create two requests.")));
  formCard = h("section", { class: "card" }, h("header", {}, h("span", { class: "label" }, t("What should the agent buy?"))), form);
  return formCard;
}

function runList() {
  return h("section", { class: "card" }, h("header", {}, h("span", { class: "label" }, t("Your requests")), h("span", { class: "muted" }, state.runs.length)),
    state.runs.length ? h("div", { style: "padding:6px 0;max-height:420px;overflow-y:auto" }, state.runs.map((r) => h("div", {
      class: "run-row" + (r.id === state.selected ? " active" : ""),
      onclick: () => { state.selected = r.id; refresh(); },
    },
      h("b", {}, `${r.input.quantity} × ${t(r.input.item)}`),
      statusChip(r.status),
      h("span", { class: "label" }, `${r.owner} · ${since(r.created_at)}${r.attempts > 1 ? ` · ${t("try")} ${r.attempts}` : ""}`)))) :
      h("div", { class: "empty" }, t("No requests yet. Start one above.")));
}

function approvalsPanel() {
  return state.approvals.map((a) => {
    const g = a.arguments;
    return h("section", { class: "approval-card" },
      h("b", { style: "font-size:16px" }, t("The agent wants to place an order")),
      h("div", {}, `${g.quantity} × ${t(g.item)} · ${g.supplier_id}`),
      h("div", { class: "row", style: "gap:18px" },
        h("span", {}, h("span", { class: "label" }, `${t("each")} `), h("b", {}, money(g.unit_price))),
        h("span", {}, h("span", { class: "label" }, `${t("total")} `), h("b", { style: "font-size:18px" }, money(g.quantity * g.unit_price)))),
      h("div", { class: "label" }, `${t("This OK only covers these exact values. Expires at")} ${new Date(a.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.`),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => decide(a, true) }, t("Approve order")),
        h("button", { class: "btn danger", onclick: () => decide(a, false) }, t("Reject"))));
  });
}

// Turns one recorded step into a sentence a non-engineer can read; the raw data stays one click away.
function describe(s) {
  const out = s.output || {};
  if (s.kind === "llm") {
    const calls = out.tool_calls || [];
    if (!calls.length) return [t("The AI wrote the summary"), out.content];
    return [`${t("The AI decided to")}: ${calls.map((c) => toolTitle(c.name).toLowerCase()).join(", ")}`, null];
  }
  if (s.kind === "approval") return [t("Paused, waiting for your OK"), null];
  if (s.kind === "system") return [t("Out of AI budget"), null];
  if (s.name === "search_suppliers" && out.suppliers) {
    return [toolTitle(s.name), `${t("Found")} ${out.suppliers.length} ${t("suppliers")}: ${out.suppliers.map((x) => x.name).join(", ")}`];
  }
  if (s.name === "get_quote") {
    return [`${toolTitle(s.name)} · ${out.supplier_id}`,
      out.error ? t("does not sell this item") : `${money(out.unit_price)} ${t("per unit")} · ${t("total")} ${money(out.total)}`];
  }
  if (out.error === "rejected_by_human") return [toolTitle(s.name), t("You rejected it, so nothing was ordered.")];
  if (s.name === "create_purchase_order" && out.po_number) {
    return [toolTitle(s.name), `${t("Order")} ${out.po_number} ${t("created")} · ${t("total")} ${money(out.total)}`];
  }
  return [toolTitle(s.name), null];
}

function stepItem(s) {
  const node = s.status === "error" ? "✕" : { llm: "AI", tool: "✓", approval: "!", system: "·" }[s.kind];
  const [title, text] = describe(s);
  return h("div", { class: `tl-item ${s.kind} ${s.status === "error" ? "error" : ""}` },
    h("div", { class: "tl-node" }, node),
    h("div", {},
      h("div", { class: "tl-head" }, h("b", {}, title), s.attempt > 1 ? h("span", { class: "chip" }, `${t("try")} ${s.attempt}`) : null),
      text ? h("div", { class: "tl-body" }, text) : null,
      h("details", { class: "tech" }, h("summary", { class: "label" }, t("Technical details")),
        h("pre", { class: "json" }, JSON.stringify({ step: s.n, name: s.name, input: s.input, output: s.output, ms: s.duration_ms }, null, 2)))));
}

function detail() {
  const r = state.run;
  if (!r) return h("section", { class: "card empty" }, t("Pick a request to see what the agent did, step by step."));
  const live = !["succeeded", "failed", "cancelled", "dead"].includes(r.status);
  return h("section", { class: "card" },
    h("header", {},
      h("div", {}, h("div", { style: "font-weight:600;font-size:18px" }, `${r.input.quantity} × ${t(r.input.item)}`),
        h("div", { class: "label" }, `${t("Request")} ${short(r.id)} · ${r.owner} · ${since(r.created_at)}`)),
      h("div", { class: "row" }, statusChip(r.status),
        live ? h("button", { class: "btn danger", onclick: () => cancel(r.id) }, t("Cancel")) : null)),
    h("div", { class: "body stack" },
      r.result?.answer ? h("div", { class: "answer" }, r.result.answer) : null,
      r.error ? h("div", { class: "approval-card", style: "border-color:rgba(255,92,108,.4);background:var(--danger-dim)" }, h("span", { class: "mono" }, JSON.stringify(r.error))) : null,
      h("div", { class: "meta-row" },
        h("span", { class: "chip" }, `${t("AI usage")}: ${num(r.tokens_in + r.tokens_out)} ${t("tokens")}`),
        r.attempts > 1 ? h("span", { class: "chip warn" }, `${t("try")} ${r.attempts}`) : null),
      h("div", { class: "label", style: "margin-top:6px" }, t("Steps")),
      state.steps.length ? h("div", { class: "timeline" }, state.steps.map(stepItem)) : h("div", { class: "muted" }, t("Waiting for a free worker…"))));
}

let ownerEl = null; // built once, like the form: a re-render must not close an open dropdown
function ownerSelect() {
  if (ownerEl) return ownerEl;
  ownerEl = h("select", { class: "owner", onchange: (e) => { state.owner = e.target.value; refresh(); } },
    OWNERS.map((o) => h("option", { value: o }, o)));
  ownerEl.value = state.owner;
  return ownerEl;
}

function render() {
  root.replaceChildren(h("div", { class: "main" },
    h("div", { class: "topbar" },
      h("div", { class: "row", style: "gap:14px" },
        h("div", { class: "brand-mark" }, h("i"), h("i"), h("i"), h("i")),
        h("div", {}, h("h1", { style: "margin:0" }, "runloop"), h("div", { class: "label" }, t("Purchasing agent")))),
      h("div", { class: "row" }, h("span", { class: "label" }, t("Buying as")), ownerSelect(),
        state.budget != null ? h("span", { class: "chip ok", title: t("AI usage this user has left in the last 24 hours") },
          `${num(state.budget)} ${t("tokens left today")}`) : null,
        langToggle(),
        h("a", { class: "btn ghost", href: "/docs", target: "_blank" }, "API"))),
    h("div", { class: "content" },
      h("p", { class: "intro" }, t("Ask the agent to buy something. It compares suppliers, then waits for your OK before placing the order. If the server crashes halfway, it picks up where it stopped and never places the same order twice.")),
      kpis(),
      h("div", { class: "grid split", style: "grid-template-columns:380px minmax(0,1fr)" },
        h("div", { class: "stack", style: "gap:16px" }, approvalsPanel(), newRunForm(), runList()),
        detail()))));
}

refresh();
setInterval(() => refresh().catch(() => {}), 1500);
