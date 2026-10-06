// Read-only widget for dashboards such as Homarr:
//   /embed?server=<id|all>&token=<EMBED_TOKEN>&theme=dark&bg=solid&show=cpu,fans,power,chart
// It reads /api/widget, which returns only the fields shared on the Homarr page; `show` narrows them
// further for this one widget. The layout follows the frame: the chart gives way first, then the
// footer, so nothing is ever cut in half.
const params = new URLSearchParams(location.search);
// $, fmt and esc come from util.js
if (["light", "dark"].includes(params.get("theme"))) document.documentElement.dataset.theme = params.get("theme");
if (params.get("bg") === "solid") document.body.classList.add("solid");
const show = params.get("show") ? new Set(params.get("show").split(",")) : null;

const query = new URLSearchParams({ server: params.get("server") || "all" });
if (params.get("token")) query.set("token", params.get("token"));

async function poll() {
  try {
    const r = await api("/api/widget?" + query);
    if (!r.ok) throw new Error(r.status === 401 ? "Add ?token= with EMBED_TOKEN" : r.status === 404 ? "Unknown server" : "Unavailable");
    render(await r.json());
  } catch (e) {
    $("#emb").innerHTML = `<div class="head"><span class="dot bad"></span><b>Fan Control</b><span class="mode">${esc(translate(e.message))}</span></div>`;
  }
}

const on = (w, field) => w.fields.includes(field) && (!show || show.has(field));
const temp = v => v == null ? "—" : `${fmt(tv(v))}<small>${tu()}</small>`;
const fans = s => s.fan_pct != null ? `${fmt(s.fan_pct)}<small>%</small>` : s.fan_rpm != null ? `${(s.fan_rpm / 1000).toFixed(1)}<small>k rpm</small>` : "—";
const dot = s => "dot " + ({ error: "bad", warn: "warn", ok: "ok" }[s.health] || "");

function render(w) {
  if (params.get("server") && params.get("server") !== "all" && w.servers.length === 1) return renderOne(w, w.servers[0]);
  // the whole rack: one line per server
  const cols = [["cpu", "CPU", s => temp(s.cpu)], ["fans", "Fans", fans], ["power", "Power", s => s.watts == null ? "—" : `${fmt(s.watts)}<small>W</small>`],
    ["inlet", "Inlet", s => temp(s.inlet)]].filter(c => on(w, c[0]));
  $("#emb").className = "emb rack";
  $("#emb").innerHTML = `<table><thead><tr><th></th>${cols.map(c => `<th>${translate(c[1])}</th>`).join("")}</tr></thead><tbody>` +
    w.servers.map(s => `<tr><td><span class="${on(w, "status") ? dot(s) : "dot"}"></span><b>${esc(s.name)}</b></td>${cols.map(c => `<td>${c[2](s)}</td>`).join("")}</tr>`).join("") +
    "</tbody></table>";
}

function renderOne(w, s) {
  const nums = [["cpu", "CPU", temp(s.cpu)], ["fans", "Fans", fans(s)],
    ["power", "Power", s.watts == null ? "—" : `${fmt(s.watts)}<small>W</small>`]].filter(n => on(w, n[0]));
  const air = [["inlet", "Inlet", s.inlet], ["exhaust", "Exhaust", s.exhaust]].filter(a => on(w, a[0]));
  $("#emb").className = "emb";
  $("#emb").innerHTML = `<div class="head"><span class="${on(w, "status") ? dot(s) : "dot"}"></span><b>${esc(s.name)}</b>
      ${on(w, "model") && s.model ? `<span class="model">${esc(s.model)}</span>` : ""}
      ${on(w, "status") ? `<span class="mode">${esc(translate(s.mode))}</span>` : ""}</div>
    ${nums.length ? `<div class="nums">${nums.map(n => `<div class="n"><label>${translate(n[1])}</label><div>${n[2]}</div></div>`).join("")}</div>` : ""}
    ${on(w, "chart") && s.history ? '<svg class="spark" aria-hidden="true"></svg>' : ""}
    ${air.length ? `<div class="row"><span>${air.map(a => `${translate(a[1])} <b>${a[2] == null ? "—" : `${fmt(tv(a[2]))} ${tu()}`}</b>`).join(" · ")}</span></div>` : ""}`;
  const svg = document.querySelector(".spark");
  if (svg) chart(svg, s.history);
}

// last hour: CPU temperature, with the fan speed underneath
function chart(svg, h) {
  const W = svg.clientWidth || 300, H = svg.clientHeight || 40;
  const line = (vals, lo, hi) => vals.map((v, i) => (i ? "L" : "M") + (i / Math.max(1, vals.length - 1) * W).toFixed(1) + ","
    + (H - 3 - (v - lo) / (hi - lo) * (H - 6)).toFixed(1)).join("");
  const lo = Math.min(...h.cpu, 30) - 2, hi = Math.max(...h.cpu, lo + 12) + 2;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `<line x1="0" x2="${W}" y1="${H - 1}" y2="${H - 1}" stroke="var(--rule)"/>
    ${h.fans.length > 1 ? `<path d="${line(h.fans, 0, 100)}" fill="none" stroke="var(--accent)" stroke-width="1.25" opacity=".8"/>` : ""}
    ${h.cpu.length > 1 ? `<path d="${line(h.cpu, lo, hi)}" fill="none" stroke="var(--cool)" stroke-width="1.5"/>` : ""}`;
}

poll();
setInterval(poll, 10000);
