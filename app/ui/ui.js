"use strict";
// ShockGraph PEA demo page. It only calls the public API (tests/test_a_ui.py checks every path it uses exists),
// and every piece of data is inserted with textContent, never parsed as HTML.

const ETFS = [
  ["FR0011869353", "Amundi PEA Monde (EWLD)"],
  ["IE0002XZSHO1", "iShares MSCI World Swap PEA (WPEA)"],
  ["FR0011871128", "Amundi PEA S&P 500 (PSP5)"],
  ["FR0011550185", "BNP Paribas Easy S&P 500 (ESE)"],
  ["FR0011871110", "Amundi PEA Nasdaq-100 (PUST)"],
  ["FR0013412038", "Amundi PEA MSCI Europe (PCEU)"],
  ["FR0013412020", "Amundi PEA MSCI Emerging (PAEEM)"],
  ["FR0007052782", "Amundi CAC 40 (CAC)"],
  ["LU1681043599", "Amundi MSCI World (CW8)"],
];

// The "diversified" student: a World ETF and an S&P 500 ETF are largely the same US companies.
const STUDENT = [
  { isin: "FR0011869353", label: "Amundi PEA Monde (EWLD)", amount_eur: 3000 },
  { isin: "FR0011871128", label: "Amundi PEA S&P 500 (PSP5)", amount_eur: 2000 },
  { isin: "FR0000120271", label: "TotalEnergies", amount_eur: 500 },
  { isin: "NL0000235190", label: "Airbus", amount_eur: 500 },
];

const ASK_PRESETS = [
  "If oil goes up 20 %, which PEA companies are the most exposed, and through which link?",
  "What moves TotalEnergies?",
  "Which stocks move like Airbus?",
  "Si le pétrole monte de 20 %, quelles entreprises de mon PEA sont les plus exposées ?",
  "Should I sell my S&P 500 ETF?",
];

const CYPHER_PRESETS = [
  ["valid read", "MATCH (c:Company)-[r:SENSITIVE_TO]->(:Factor {code: 'BRENT'})\nRETURN c.name, r.beta ORDER BY r.beta DESC LIMIT 10"],
  ["write", "MATCH (c:Company) DETACH DELETE c"],
  ["unbounded path", "MATCH (a:Company)-[:CORRELATED_WITH*]-(b:Company) RETURN b.name LIMIT 5"],
  ["UNION without LIMIT", "MATCH (c:Company) RETURN c.name AS n\nUNION MATCH (f:Fund) RETURN f.name AS n LIMIT 5"],
  ["Unicode dash", "MATCH (a:Company)–[:CORRELATED_WITH*]–(b:Company) RETURN b.name LIMIT 5"],
];

const state = { positions: STUDENT.map((position) => ({ ...position })), factors: [] };

// ---------- helpers ----------

/** Build an element; text children become text nodes, so data is never interpreted as HTML. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);
const show = (target, ...nodes) => (typeof target === "string" ? $(target) : target).replaceChildren(...nodes.flat(Infinity).filter(Boolean));

const euros = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const signed = (value, digits = 1, unit = " %") =>
  value === null || value === undefined ? "–" : `${value > 0 ? "+" : ""}${Number(value).toFixed(digits)}${unit}`;
const share = (weight) => `${(weight * 100).toFixed(1)} %`;
const fixed = (value, digits = 2) => (value === null || value === undefined ? "–" : Number(value).toFixed(digits));
const signClass = (value) => (value > 0 ? "pos" : value < 0 ? "neg" : "");

const badge = (truth) => el("span", { class: `badge ${truth}` }, truth);
const kpi = (value, label, extra = "") => el("div", { class: "kpi" }, el("div", { class: `value ${extra}` }, value), el("div", { class: "label" }, label));
const errorBox = (error) => el("div", { class: "error" }, error.message || String(error));
const warnings = (result) => (result.warnings || []).map((text) => el("p", { class: "warn" }, text));

/** Call the API; a non-2xx answer becomes an Error with the API's own message (detail and field errors). */
async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: { Accept: "application/json" } };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  if (options.apiKey) init.headers["X-API-Key"] = options.apiKey;
  const response = await fetch(path, init);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (response.status === 504) {
    // Nginx gives up after 3 min (proxy_read_timeout) and answers in HTML, so there is no detail to show.
    throw new Error("504 · no answer within the proxy's 3 min. On a CPU the 7b model is slow: ask a shorter question or use a GPU machine.");
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${response.status}`;
    const fields = data && Array.isArray(data.errors) ? data.errors.map((item) => `${item.field}: ${item.message}`).join("; ") : "";
    throw new Error(`${response.status} · ${detail}${fields ? ` (${fields})` : ""}`);
  }
  return data;
}

/** Run a task behind a button: loading state, result or error in the target, button re-enabled. */
async function run(button, target, task) {
  button.disabled = true;
  const started = Date.now();
  const loading = el("p", { class: "muted" }, "Loading…");
  show(target, loading);
  // A local agent can take minutes: count the seconds so a slow answer does not look like a frozen page.
  const timer = setInterval(() => {
    loading.textContent = `Loading… ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  try {
    show(target, await task());
  } catch (error) {
    show(target, errorBox(error));
  } finally {
    clearInterval(timer);
    button.disabled = false;
  }
}

const companyLink = (isin, name) => el("button", { class: "link", onclick: () => openCompany(isin) }, name || isin);
const factorName = (code) => (state.factors.find((factor) => factor.code === code) || {}).name || code;

// ---------- tabs ----------

function openTab(name) {
  const known = [...document.querySelectorAll(".tabs button")].map((button) => button.dataset.tab);
  const tab = known.includes(name) ? name : "portfolio";
  document.querySelectorAll(".tabs button").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  document.querySelectorAll(".tab-panel").forEach((panel) => (panel.hidden = panel.id !== `tab-${tab}`));
  document.querySelector(".positions").hidden = !["portfolio", "shock"].includes(tab);
  if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
}

// ---------- positions ----------

function renderPositions() {
  const rows = state.positions.map((position, index) =>
    el(
      "tr",
      {},
      el("td", {}, position.label || ""),
      el("td", {}, el("code", {}, position.isin)),
      el(
        "td",
        { class: "num" },
        el("input", {
          type: "number", min: "1", step: "100", value: position.amount_eur, style: "width: 110px",
          "aria-label": `Amount for ${position.label || position.isin}`,
          oninput: (event) => (position.amount_eur = Number(event.target.value)),
        }),
      ),
      el("td", { class: "num" }, el("button", { class: "ghost", onclick: () => removePosition(index) }, "remove")),
    ),
  );
  show($("positions-table").tBodies[0], rows);
}

function removePosition(index) {
  state.positions.splice(index, 1);
  renderPositions();
}

function addPosition(isin, label) {
  if (!state.positions.some((position) => position.isin === isin)) state.positions.push({ isin, label, amount_eur: 1000 });
  renderPositions();
}

const positionsBody = () =>
  state.positions.filter((position) => position.amount_eur > 0).map(({ isin, amount_eur }) => ({ isin, amount_eur }));

let searchTimer = null;
function searchCompanies(input) {
  clearTimeout(searchTimer);
  const list = $("company-results");
  const query = input.value.trim();
  if (query.length < 2) {
    list.hidden = true;
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const page = await api(`/api/companies?q=${encodeURIComponent(query)}&limit=8`);
      show(
        list,
        page.items.map((company) =>
          el(
            "li",
            {
              onclick: () => {
                addPosition(company.isin, company.name);
                list.hidden = true;
                input.value = "";
              },
            },
            company.name,
            " ",
            el("span", { class: "muted small" }, [company.ticker, company.country, company.pea_eligible ? "PEA" : "not PEA"].filter(Boolean).join(" · ")),
          ),
        ),
      );
      list.hidden = page.items.length === 0;
    } catch (error) {
      show(list, el("li", { class: "muted" }, error.message));
      list.hidden = false;
    }
  }, 250);
}

// ---------- 1 · x-ray ----------

function bars(lines, max = 8) {
  return el(
    "div",
    { class: "bars" },
    lines.slice(0, max).map((line) =>
      el(
        "div",
        { class: "bar" },
        el("span", { class: "name", title: line.label }, line.label),
        el("span", { class: "track" }, el("span", { class: "fill", style: `width: ${Math.min(100, line.weight * 100).toFixed(1)}%` })),
        el("span", { class: "num" }, share(line.weight)),
      ),
    ),
  );
}

async function runXray() {
  const result = await api("/api/portfolio/xray", { method: "POST", body: { positions: positionsBody() } });
  const us = result.countries.find((country) => country.key === "US");
  const top10 = result.companies.slice(0, 10).reduce((total, company) => total + company.weight, 0);
  return [
    el(
      "div",
      { class: "kpis" },
      kpi(euros.format(result.total_eur), "invested"),
      kpi(result.companies.length.toLocaleString("fr-FR"), "companies after look-through"),
      us ? kpi(share(us.weight), "in US companies") : null,
      result.companies.length ? kpi(share(top10), "in the 10 largest lines") : null,
    ),
    warnings(result),
    result.unknown_isins.length ? el("p", { class: "warn" }, `Unknown ISINs: ${result.unknown_isins.join(", ")}`) : null,
    el(
      "div",
      { class: "cols" },
      el(
        "div",
        {},
        el("h3", {}, "Largest lines"),
        el(
          "table",
          { class: "grid" },
          el("thead", {}, el("tr", {}, el("th", {}, "Company"), el("th", { class: "num" }, "€"), el("th", { class: "num" }, "Weight"))),
          el(
            "tbody",
            {},
            result.companies.slice(0, 15).map((line) =>
              el("tr", {}, el("td", {}, companyLink(line.key, line.label)), el("td", { class: "num" }, euros.format(line.amount_eur)), el("td", { class: "num" }, share(line.weight))),
            ),
          ),
        ),
      ),
      el("div", {}, el("h3", {}, "Countries"), bars(result.countries), el("h3", {}, "Sectors"), bars(result.sectors)),
    ),
    result.factors.length
      ? [
          el("h3", {}, "Sensitivity of the whole portfolio ", badge("estimate")),
          el("p", { class: "hint" }, "Betas weighted by the euros invested; 'covered' is the share of the portfolio that has an estimate."),
          el(
            "table",
            { class: "grid" },
            el("thead", {}, el("tr", {}, el("th", {}, "Factor"), el("th", { class: "num" }, "Weighted beta"), el("th", { class: "num" }, "Covered"))),
            el(
              "tbody",
              {},
              result.factors.map((line) =>
                el("tr", {}, el("td", {}, factorName(line.factor_code)), el("td", { class: `num ${signClass(line.weighted_beta)}` }, fixed(line.weighted_beta, 3)), el("td", { class: "num" }, share(line.covered_weight))),
              ),
            ),
          ),
        ]
      : null,
    el("p", { class: "hint" }, result.disclaimer),
  ];
}

// ---------- 2 · shock ----------

function impactTable(lines, factorCode) {
  const sorted = [...lines].sort((a, b) => Math.abs(b.impact_pct ?? -1) - Math.abs(a.impact_pct ?? -1));
  const shown = sorted.slice(0, 60);
  return [
    el(
      "table",
      { class: "grid" },
      el(
        "thead",
        {},
        el("tr", {}, el("th", {}, "Company"), el("th", {}, "Truth"), el("th", { class: "num" }, "Impact"), el("th", { class: "num" }, "On my €"), el("th", {}, "Why"), el("th", {})),
      ),
      el(
        "tbody",
        {},
        shown.map((line) =>
          el(
            "tr",
            {},
            el("td", {}, companyLink(line.isin, line.name)),
            el("td", {}, badge(line.truth)),
            el("td", { class: `num ${signClass(line.impact_pct)}` }, line.impact_pct === null ? "direction only" : signed(line.impact_pct)),
            el("td", { class: `num ${signClass(line.impact_eur)}` }, line.impact_eur === null || line.impact_eur === undefined ? "" : euros.format(line.impact_eur)),
            el("td", { class: "small" }, line.explanation, line.path.length ? el("div", { class: "refs" }, line.path.join(" → ")) : null),
            el("td", {}, el("button", { class: "ghost", onclick: () => explain(`Factor:${factorCode}`, `Company:${line.isin}`) }, "path")),
          ),
        ),
      ),
    ),
    sorted.length > shown.length ? el("p", { class: "hint" }, `${sorted.length - shown.length} more lines not shown.`) : null,
  ];
}

async function runShock() {
  const factorCode = $("shock-factor").value;
  const body = {
    shocks: [{ factor_code: factorCode, pct: Number($("shock-pct").value) }],
    depth: Number($("shock-depth").value),
    min_r2: Number($("shock-r2").value),
  };
  if ($("shock-use-positions").checked) body.positions = positionsBody();
  const result = await api("/api/shocks/simulate", { method: "POST", body });
  return [
    body.positions
      ? el(
          "div",
          { class: "kpis" },
          kpi(result.portfolio_impact_eur === null ? "–" : euros.format(result.portfolio_impact_eur), "estimated impact on my portfolio", signClass(result.portfolio_impact_eur)),
          kpi(signed(result.portfolio_impact_pct, 2), "of the portfolio", signClass(result.portfolio_impact_pct)),
          kpi(String(result.direct.length), "companies hit directly"),
        )
      : null,
    warnings(result),
    el("h3", {}, `Direct exposure to ${factorName(factorCode)} (${result.direct.length})`),
    impactTable(result.direct, factorCode),
    result.indirect.length
      ? [el("h3", {}, `One step further, through news-based supplier links (${result.indirect.length})`), impactTable(result.indirect, factorCode)]
      : null,
    el("p", { class: "hint" }, result.disclaimer),
  ];
}

// ---------- 3 · path ----------

async function refName(ref) {
  const [label, key] = ref.split(":");
  if (label === "Factor") return factorName(key);
  if (label === "Company") {
    try {
      return (await api(`/api/companies/${encodeURIComponent(key)}`)).name;
    } catch {
      return key;
    }
  }
  return key;
}

function svg(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The path as a chain of boxes; each link is coloured and labelled with its truth level. */
function chain(result, names) {
  const boxWidth = 160;
  const gap = 130;
  const height = 96;
  const width = result.nodes.length * boxWidth + (result.nodes.length - 1) * gap;
  const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Explained path" });
  result.nodes.forEach((ref, index) => {
    const x = index * (boxWidth + gap);
    root.append(svg("rect", { x: x + 1, y: 20, width: boxWidth - 2, height: 52, rx: 8, class: "node" }));
    root.append(svg("text", { x: x + boxWidth / 2, y: 42, "text-anchor": "middle", "font-weight": "600" }, names[index].slice(0, 22)));
    root.append(svg("text", { x: x + boxWidth / 2, y: 60, "text-anchor": "middle", opacity: "0.65" }, ref.split(":")[0]));
    if (index < result.relations.length) {
      const truth = result.truth_levels[index];
      const startX = x + boxWidth;
      // No arrowhead: the API gives the relation type, not its direction (an industry CONSUMES a factor).
      root.append(svg("line", { x1: startX + 4, y1: 46, x2: startX + gap - 4, y2: 46, style: `stroke: var(--${truth}); stroke-width: 2` }));
      root.append(svg("text", { x: startX + gap / 2, y: 38, "text-anchor": "middle", class: "rel" }, result.relations[index]));
      root.append(svg("text", { x: startX + gap / 2, y: 64, "text-anchor": "middle", class: "rel", style: `fill: var(--${truth})` }, truth));
    }
  });
  return el("div", { class: "chain" }, root);
}

async function runPath() {
  const params = new URLSearchParams({ from: $("path-from").value.trim(), to: $("path-to").value.trim(), max_hops: $("path-hops").value });
  const result = await api(`/api/paths?${params}`);
  if (!result.nodes || !result.nodes.length) return el("p", { class: "muted" }, "No path within this number of hops.");
  const names = await Promise.all(result.nodes.map(refName));
  return [
    el("p", {}, `${result.hops} hop${result.hops > 1 ? "s" : ""} from ${names[0]} to ${names[names.length - 1]}.`),
    chain(result, names),
    el("p", { class: "hint" }, "A chain is only as strong as its weakest link: an estimate or a hypothesis anywhere means the whole explanation is not a fact."),
  ];
}

function explain(fromRef, toRef) {
  $("path-from").value = fromRef;
  $("path-to").value = toRef;
  openTab("path");
  run($("run-path"), "path-out", runPath);
}

// ---------- 4 · network ----------

async function runNetwork() {
  const button = $("run-network");
  button.disabled = true;
  show("systemic-out", el("p", { class: "muted" }, "Loading…"));
  show("clusters-out");
  try {
    const [systemic, clusters] = await Promise.all([api("/api/network/systemic?limit=15"), api("/api/network/clusters?limit=6")]);
    show(
      "systemic-out",
      el("h3", {}, "Most central stocks (PageRank)"),
      el(
        "table",
        { class: "grid" },
        el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Company"), el("th", { class: "num" }, "Score"))),
        el("tbody", {}, systemic.map((line, index) => el("tr", {}, el("td", {}, index + 1), el("td", {}, companyLink(line.isin, line.name)), el("td", { class: "num" }, fixed(line.score, 3))))),
      ),
    );
    show(
      "clusters-out",
      el("h3", {}, "Communities of stocks that move together (Louvain)"),
      clusters.map((cluster) =>
        el(
          "div",
          { class: "cluster" },
          el("strong", {}, `Community ${cluster.community_id}`),
          el("span", { class: "muted" }, ` · ${cluster.size} stocks`),
          cluster.top_factors.length ? el("div", { class: "small muted" }, `Shared factors: ${cluster.top_factors.map(factorName).join(", ")}`) : null,
          el("div", { class: "small" }, cluster.members.slice(0, 8).map((member, index) => [index ? ", " : "", companyLink(member.isin, member.name)])),
        ),
      ),
    );
  } catch (error) {
    show("systemic-out", errorBox(error));
  } finally {
    button.disabled = false;
  }
}

// ---------- company drawer ----------

function exposureRows(exposures) {
  return exposures.map((line) =>
    el(
      "tr",
      {},
      el("td", {}, line.factor_name || line.factor_code),
      el("td", { class: "small" }, line.relation),
      el("td", {}, badge(line.truth)),
      el("td", { class: `num ${signClass(line.beta)}` }, line.beta === null ? "" : fixed(line.beta, 2)),
      el("td", { class: "num" }, line.r2 !== null ? fixed(line.r2, 2) : line.prob !== null ? `p ${fixed(line.prob, 2)}` : ""),
    ),
  );
}

async function openCompany(isin) {
  $("drawer").hidden = false;
  show("drawer-body", el("p", { class: "muted" }, "Loading…"));
  const key = encodeURIComponent(isin);
  try {
    const [detail, exposures, similar, predicted] = await Promise.all([
      api(`/api/companies/${key}`),
      api(`/api/companies/${key}/exposures`),
      api(`/api/companies/${key}/similar?k=5`).catch((error) => error),
      api(`/api/companies/${key}/predicted-exposures`).catch((error) => error),
    ]);
    const cap = detail.market_cap_eur ? `float market cap ≈ ${(detail.market_cap_eur / 1e9).toFixed(0)} bn €` : null;
    show(
      "drawer-body",
      el("h2", {}, detail.name),
      el("p", { class: "muted small" }, [detail.ticker, detail.sector, detail.industry, detail.country, detail.pea_eligible ? "PEA-eligible" : "not PEA-eligible"].filter(Boolean).join(" · ")),
      cap ? el("p", { class: "small" }, cap) : null,
      detail.indexes.length ? el("p", { class: "small" }, `In: ${detail.indexes.join(", ")}`) : null,
      el("h3", {}, "Exposures"),
      exposures.length
        ? el(
            "table",
            { class: "grid" },
            el("thead", {}, el("tr", {}, el("th", {}, "Factor"), el("th", {}, "Link"), el("th", {}, "Truth"), el("th", { class: "num" }, "Beta"), el("th", { class: "num" }, "r² / p"))),
            el("tbody", {}, exposureRows(exposures)),
          )
        : el("p", { class: "muted small" }, "No significant estimate and no declared link."),
      el("h3", {}, "Predicted by the ML model ", badge("prediction")),
      predicted instanceof Error
        ? el("p", { class: "muted small" }, predicted.message)
        : predicted.length
          ? el("table", { class: "grid" }, el("tbody", {}, exposureRows(predicted)))
          : el("p", { class: "muted small" }, "No prediction above the threshold."),
      el("h3", {}, "Moves like (FastRP embeddings)"),
      similar instanceof Error
        ? el("p", { class: "muted small" }, similar.message)
        : el("ul", { class: "small" }, similar.map((item) => el("li", {}, companyLink(item.isin, item.name), el("span", { class: "muted" }, ` · ${fixed(item.score, 3)}`)))),
      el("p", {}, el("button", { class: "ghost", onclick: () => explain("Factor:BRENT", `Company:${isin}`) }, "How does Brent reach it?")),
    );
  } catch (error) {
    show("drawer-body", errorBox(error));
  }
}

// ---------- 5 · agent ----------

function readKey() {
  try {
    return sessionStorage.getItem("shockgraph-api-key") || "";
  } catch {
    return "";
  }
}

function saveKey(value) {
  try {
    sessionStorage.setItem("shockgraph-api-key", value);
  } catch {
    // Private mode: the key simply is not remembered.
  }
}

function claimBlock(claim, removed = false) {
  return el(
    "div",
    { class: `claim ${claim.truth}${removed ? " removed" : ""}` },
    badge(claim.truth),
    " ",
    claim.text,
    claim.evidence.length ? el("div", { class: "refs" }, claim.evidence.join(" · ")) : null,
    // Set by the verifier: why the claim was removed, or why its truth level was lowered.
    claim.reason ? el("div", { class: "reason" }, `Verifier: ${claim.reason}`) : null,
  );
}

async function runAsk() {
  const result = await api("/api/ask", { method: "POST", body: { question: $("ask-question").value.trim() }, apiKey: $("api-key").value.trim() });
  return [
    result.refused ? el("p", {}, el("span", { class: "badge bad" }, "refused"), " ", result.refusal_reason || "") : null,
    el("div", { class: "answer" }, result.answer),
    result.claims.length ? [el("h3", {}, `Claims (${result.claims.length})`), result.claims.map((claim) => claimBlock(claim))] : null,
    result.removed_claims.length
      ? [el("h3", {}, `Removed by the verifier (${result.removed_claims.length})`), result.removed_claims.map((claim) => claimBlock(claim, true))]
      : null,
    result.trace.length
      ? el(
          "details",
          { open: true },
          el("summary", {}, `Trace: ${result.trace.length} tool call${result.trace.length > 1 ? "s" : ""}`),
          el(
            "table",
            { class: "grid" },
            el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Tool"), el("th", {}, "Arguments"), el("th", { class: "num" }, "ms"), el("th", {}, "Result"))),
            el(
              "tbody",
              {},
              result.trace.map((call, index) =>
                el(
                  "tr",
                  {},
                  el("td", {}, index + 1),
                  el("td", {}, el("code", {}, call.tool)),
                  el("td", {}, el("code", { class: "small" }, JSON.stringify(call.args))),
                  el("td", { class: "num" }, call.duration_ms),
                  el("td", { class: "small" }, call.error ? el("span", { class: "neg" }, call.error) : call.result_preview),
                ),
              ),
            ),
          ),
        )
      : null,
    el("p", { class: "hint" }, result.disclaimer),
  ];
}

async function runCypher() {
  const result = await api("/api/agent/validate-cypher", { method: "POST", body: { cypher: $("cypher").value } });
  return result.valid
    ? el("p", {}, el("span", { class: "badge good" }, "accepted"), " read-only, bounded, known schema")
    : [el("p", {}, el("span", { class: "badge bad" }, "refused")), el("ul", {}, result.reasons.map((reason) => el("li", {}, reason)))];
}

// ---------- start ----------

async function loadHealth() {
  const box = $("status");
  try {
    const health = await api("/api/health");
    box.textContent = `${health.status === "ok" ? "online" : "degraded"} · Neo4j ${health.neo4j ? "✓" : "✗"} · GDS ${health.gds ? "✓" : "✗"} · v${health.version}`;
    box.className = `status ${health.status === "ok" ? "ok" : "bad"}`;
  } catch (error) {
    box.textContent = `API unreachable (${error.message})`;
    box.className = "status bad";
  }
}

async function loadFactors() {
  const select = $("shock-factor");
  try {
    state.factors = await api("/api/factors");
    const kinds = [...new Set(state.factors.map((factor) => factor.kind))];
    show(
      select,
      kinds.map((kind) =>
        el("optgroup", { label: kind }, state.factors.filter((factor) => factor.kind === kind).map((factor) => el("option", { value: factor.code, selected: factor.code === "BRENT" }, `${factor.name} (${factor.code})`))),
      ),
    );
  } catch (error) {
    show(select, el("option", { value: "BRENT" }, "Brent crude oil (BRENT)"));
  }
}

function init() {
  document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => openTab(button.dataset.tab)));
  window.addEventListener("hashchange", () => openTab(location.hash.slice(1)));

  ETFS.forEach(([isin, label]) => $("etf-select").append(el("option", { value: isin }, label)));
  $("etf-select").addEventListener("change", (event) => {
    const option = event.target.selectedOptions[0];
    if (option.value) addPosition(option.value, option.textContent);
    event.target.value = "";
  });
  $("company-search").addEventListener("input", (event) => searchCompanies(event.target));
  $("preset-student").addEventListener("click", () => {
    state.positions = STUDENT.map((position) => ({ ...position }));
    renderPositions();
  });
  $("clear-positions").addEventListener("click", () => {
    state.positions = [];
    renderPositions();
  });

  $("run-xray").addEventListener("click", (event) => run(event.currentTarget, "xray-out", runXray));
  $("run-shock").addEventListener("click", (event) => run(event.currentTarget, "shock-out", runShock));
  $("run-path").addEventListener("click", (event) => run(event.currentTarget, "path-out", runPath));
  $("run-network").addEventListener("click", runNetwork);
  $("run-ask").addEventListener("click", (event) => run(event.currentTarget, "ask-out", runAsk));
  $("run-cypher").addEventListener("click", (event) => run(event.currentTarget, "cypher-out", runCypher));

  $("api-key").value = readKey();
  $("api-key").addEventListener("change", (event) => saveKey(event.target.value));
  ASK_PRESETS.forEach((question) => $("ask-presets").append(el("button", { class: "ghost small", onclick: () => ($("ask-question").value = question) }, question.length > 46 ? `${question.slice(0, 44)}…` : question)));
  CYPHER_PRESETS.forEach(([label, cypher]) => $("cypher-presets").append(el("button", { class: "ghost small", onclick: () => ($("cypher").value = cypher) }, label)));
  $("cypher").value = CYPHER_PRESETS[0][1];

  $("drawer-close").addEventListener("click", () => ($("drawer").hidden = true));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") $("drawer").hidden = true;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".search")) $("company-results").hidden = true;
  });

  renderPositions();
  openTab(location.hash.slice(1));
  loadHealth();
  loadFactors();
}

init();
