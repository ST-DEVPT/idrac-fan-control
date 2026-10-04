const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const T_MIN = 20, T_MAX = 95;

let overview = null;                 // /api/overview: every server, the drivers, the build
let server = null, draft = null, dirty = false, range = 3600, drag = null;
let route = { view: "overview" };

const fmt = (v, d = 0) => v == null ? "—" : Number(v).toFixed(d);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const time = (t, sec) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(sec && { second: "2-digit" }) });
const unit = (v, u, d = 0) => v == null ? "—" : `${fmt(v, d)}<small>${u}</small>`;
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";
const api = (url, data) => fetch(url, data === undefined ? { cache: "no-store" } :
  { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function toast(msg, error) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (error ? " error" : "");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.className = "toast", 3200);
}

function curveSpeed(curve, temp) {
  const p = [...curve].sort((a, b) => a[0] - b[0]);
  if (temp <= p[0][0]) return p[0][1];
  for (let i = 1; i < p.length; i++) if (temp <= p[i][0]) {
    const [t0, s0] = p[i - 1], [t1, s1] = p[i];
    return t1 > t0 ? Math.round(s0 + (s1 - s0) * (temp - t0) / (t1 - t0)) : s1;
  }
  return p[p.length - 1][1];
}

// What a server is doing, in a few words and a colour, for cards, the sidebar and headers.
function status(x) {
  if (x.error) return { cls: "bad", text: "Unreachable" };
  if (!x.updated) return { cls: "", text: "Connecting…" };
  if (x.failsafe) return { cls: "warn", text: "Failsafe · automatic" };
  if (x.effective === "monitor") return { cls: "ok", text: "Monitoring" };
  if (x.effective === "auto") return { cls: "ok", text: "Automatic" };
  return { cls: x.dry_run ? "warn" : "ok", text: `${x.dry_run ? "Dry run · " : ""}${cap(x.mode)} · ${x.applied_speed}%` };
}

// ---------------------------------------------------------------- router
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "server" && parts[1]) return { view: "server", id: parts[1] };
  if (parts[0] === "add") return { view: "edit", driver: parts[1] || "" };
  if (parts[0] === "edit" && parts[1]) return { view: "edit", id: parts[1] };
  if (parts[0] === "alerts") return { view: "alerts" };
  if (["prometheus", "grafana", "homarr"].includes(parts[0])) return { view: parts[0] };
  return { view: "overview" };
}

function go() {
  const next = parseRoute();
  if (dirty && route.view === "server" && (next.view !== "server" || next.id !== route.id)) {
    if (!confirm("Discard unsaved fan settings?")) { history.back(); return; }
    dirty = false;
  }
  if (next.view === "server" && next.id !== route.id) { server = null; draft = null; }
  route = next;
  $$(".view").forEach(v => v.hidden = v.id !== "v-" + route.view);
  $$(".side-nav a").forEach(a => a.setAttribute("aria-current", a.dataset.route === route.view));
  renderSide();
  if (route.view === "server") pollServer();
  if (route.view === "edit") openEditor();
  if (route.view === "overview") renderOverview();
  if (["prometheus", "grafana", "homarr"].includes(route.view)) renderIntegration(route.view);
  const title = { overview: "Overview", alerts: "Discord", prometheus: "Prometheus", grafana: "Grafana", homarr: "Homarr",
                  edit: route.id ? "Edit server" : "Add a server" }[route.view];
  if (title) document.title = `${title} · Fan Control`;
  scrollTo(0, 0);
}
addEventListener("hashchange", go);

// ---------------------------------------------------------------- data
async function pollOverview() {
  try {
    const r = await api("/api/overview");
    if (r.status === 401) return location.replace("/login");
    overview = await r.json();
    $("#signout").hidden = !overview.auth;
    $("#version").textContent = /^\d/.test(overview.version) ? "v" + overview.version : overview.version;
    renderSide();
    if (route.view === "overview") renderOverview();
  } catch { /* the server view shows the connection state */ }
}

async function pollServer() {
  if (route.view !== "server") return;
  const id = route.id;
  try {
    const r = await api("/api/state?server=" + encodeURIComponent(id));
    if (r.status === 401) return location.replace("/login");
    if (r.status === 404) { toast("That server no longer exists", true); location.hash = "#/"; return; }
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    if (route.view !== "server" || route.id !== id) return;
    server = s;
    if (!dirty) draft = structuredClone(server.settings);
    renderServer();
  } catch {
    $("#dot-link").className = "dot bad";
    $("#link").textContent = "Dashboard offline";
  }
}

// ---------------------------------------------------------------- sidebar
function renderSide() {
  if (!overview) return;
  $("#side-servers").innerHTML = overview.servers.map(x => {
    const st = status(x);
    return `<a href="#/server/${encodeURIComponent(x.id)}" aria-current="${route.view === "server" && route.id === x.id}">
      <span class="dot ${st.cls}"></span><span class="nm">${esc(x.name)}</span>
      <span class="t">${x.cpu_temp == null ? "" : fmt(x.cpu_temp) + "°"}</span></a>`;
  }).join("") || '<p class="side-empty">No servers yet</p>';
}

// ---------------------------------------------------------------- overview
function renderOverview() {
  if (!overview) return;
  const list = overview.servers;
  $("#welcome").hidden = list.length > 0;
  $("#fleet").hidden = !list.length;
  if (!list.length) {
    $("#ov-lede").textContent = "Nothing to watch yet.";
    $("#welcome-tiles").innerHTML = tiles(overview.drivers, null, true);
    return;
  }
  const temps = list.map(x => x.cpu_temp).filter(v => v != null);
  const watts = list.map(x => x.watts).filter(v => v != null);
  const issues = list.filter(x => x.error || x.failsafe).length;
  $("#ov-lede").textContent = [
    `${list.length} server${list.length > 1 ? "s" : ""}`,
    temps.length ? `hottest CPU ${fmt(Math.max(...temps))} °C` : "",
    watts.length ? `${fmt(watts.reduce((a, b) => a + b))} W in total` : "",
    issues ? `${issues} need${issues > 1 ? "" : "s"} attention` : "all fine",
  ].filter(Boolean).join(" · ");
  $("#fleet").innerHTML = list.map(card).join("") +
    `<a class="card add" href="#/add"><span class="plus">+</span><span>Add a server</span></a>`;
}

function card(x) {
  const st = status(x);
  const fans = x.effective === "manual" ? `${x.applied_speed}%` : x.fan_pct != null ? `${x.fan_pct}%` :
    x.fan_rpm != null ? `${(x.fan_rpm / 1000).toFixed(1)}k rpm` : "—";
  const badge = x.control ? (x.experimental ? "Fan control · experimental" : "Fan control") : "Monitoring";
  return `<a class="card${x.error ? " is-bad" : x.failsafe ? " is-warn" : ""}" href="#/server/${encodeURIComponent(x.id)}">
    <div class="card-top"><span class="dot ${st.cls}"></span><b>${esc(x.name)}</b><span class="vendor">${esc(x.vendor)}</span></div>
    <div class="card-sub">${esc(x.model || x.driver_label)}${x.host ? " · " + esc(x.host) : ""}</div>
    <div class="card-main">
      <div class="big${x.cpu_temp != null && x.cpu_temp >= 70 ? " hot" : ""}">${unit(x.cpu_temp, "°C")}</div>
      <dl><div><dt>Fans</dt><dd>${fans}</dd></div><div><dt>Power</dt><dd>${x.watts == null ? "—" : fmt(x.watts) + " W"}</dd></div>
        <div><dt>Inlet</dt><dd>${x.inlet == null ? "—" : fmt(x.inlet) + " °C"}</dd></div></dl>
    </div>
    ${cardSpark(x.spark)}
    <div class="card-foot"><span class="state ${st.cls}">${esc(x.error ? "Unreachable: " + x.error : st.text)}</span><span class="badge">${badge}</span></div>
  </a>`;
}

function cardSpark(pts) {
  const W = 300, H = 44, temps = pts.map(p => p[1]).filter(v => v != null);
  if (temps.length < 2) return `<svg class="card-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="0" x2="${W}" y1="${H - 1}" y2="${H - 1}"/></svg>`;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
  const lo = Math.min(...temps) - 2, hi = Math.max(Math.max(...temps), lo + 10) + 2;
  const x = t => (t - t0) / Math.max(1, t1 - t0) * W, y = v => H - 3 - (v - lo) / (hi - lo) * (H - 8);
  const d = pts.filter(p => p[1] != null).map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  return `<svg class="card-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <path class="area" d="${d}L${W},${H}L0,${H}Z"/><path class="ln" d="${d}"/></svg>`;
}

function tiles(drivers, selected, links) {
  return drivers.map(d => {
    const tags = [d.control ? "Fan control" : "Monitoring", d.experimental ? "Experimental" : ""].filter(Boolean);
    const inner = `<b>${esc(d.label)}</b><span>${esc(d.description)}</span>
      <span class="tags">${tags.map(t => `<i class="${t === "Experimental" ? "exp" : t === "Monitoring" ? "mon" : "ctl"}">${t}</i>`).join("")}</span>`;
    return links
      ? `<a class="tile" href="#/add/${d.kind}">${inner}</a>`
      : `<button type="button" class="tile" role="radio" data-kind="${d.kind}" aria-checked="${d.kind === selected}">${inner}</button>`;
  }).join("");
}

// ---------------------------------------------------------------- one server
function renderServer() {
  const s = server, sens = s.sensors || { temps: [], fans: [] };
  const auto = s.effective === "auto", monitor = s.effective === "monitor" || !s.control;
  const rpms = sens.fans.map(f => f.rpm).filter(v => v != null);
  const pcts = sens.fans.map(f => f.pct).filter(v => v != null);
  const avgRpm = rpms.length ? rpms.reduce((a, b) => a + b) / rpms.length : null;
  const avgPct = pcts.length ? pcts.reduce((a, b) => a + b) / pcts.length : null;

  document.title = `${s.name} · Fan Control`;
  $("#s-name").textContent = s.name;
  $("#s-vendor").textContent = s.driver_label;
  $("#s-model").textContent = s.model || s.driver_label;
  $("#host").textContent = s.driver === "demo" ? "simulated data" : s.host === "local" ? "local BMC" : s.host;
  $("#s-edit").hidden = s.source === "environment";
  $("#s-edit").href = "#/edit/" + encodeURIComponent(s.id);
  const stale = !s.updated || Date.now() / 1000 - s.updated > s.interval * 3;
  $("#dot-link").className = "dot " + (s.error ? "bad" : stale ? "warn" : "ok");
  $("#link").textContent = s.error ? "BMC error" : stale ? "Waiting for readings" : `Read ${Math.max(0, Math.round(Date.now() / 1000 - s.updated))} s ago`;
  $("#dot-power").className = "dot " + (s.power === "on" ? "ok" : s.power === "off" ? "warn" : "");
  $("#power").textContent = s.power === "on" ? "Powered on" : s.power === "off" ? "Powered off" : "Power —";
  $("#mode-now").textContent = (s.dry_run ? "Dry run · " : "") +
    (monitor ? "Monitoring" : auto ? "Automatic" : `${cap(s.settings.mode)} · ${s.applied_speed} %`);

  const alert = s.error ? ["BMC", s.error] : s.failsafe ? ["Failsafe", `${cap(s.reason)}. The BMC is controlling the fans.`] : null;
  $("#alert").classList.toggle("show", !!alert);
  if (alert) { $("#alert-title").textContent = alert[0]; $("#alert-msg").textContent = alert[1]; }

  const cpus = sens.temps.filter(t => t.cpu).sort((a, b) => b.value - a.value);
  const fs = draft.failsafe_temp;
  $("#v-cpu").innerHTML = unit(s.cpu_temp, "°C");
  $("#v-cpu").classList.toggle("hot", s.cpu_temp != null && s.cpu_temp >= fs - 5);
  $("#cpu-name").textContent = cpus.length > 1 ? cpus.map(c => `${c.name.replace("CPU ", "#")} ${fmt(c.value)}°`).join(" · ") : "";
  $("#s-cpu").textContent = s.cpu_temp == null ? "" : monitor ? (cpus[0]?.name || "") : `${fmt(fs - s.cpu_temp)} °C below failsafe`;
  $("#v-speed").innerHTML = monitor ? unit(avgPct, "%") : auto ? "Auto" : unit(s.applied_speed, "%");
  $("#s-speed").textContent = monitor ? "set by the BMC" : cap(s.reason);
  $("#lbl-fanavg").textContent = rpms.length ? "Average speed" : "Fans reporting";
  $("#v-rpm").innerHTML = avgRpm != null ? `${(avgRpm / 1000).toFixed(1)}<small>k rpm</small>` : unit(sens.fans.length || null, "fans");
  $("#s-rpm").textContent = rpms.length ? `${Math.min(...rpms).toLocaleString()}–${Math.max(...rpms).toLocaleString()} across ${rpms.length} fans`
    : pcts.length ? `${Math.min(...pcts)}–${Math.max(...pcts)} %` : "";
  $("#v-inlet").innerHTML = unit(sens.inlet, "°C");
  $("#v-exhaust").innerHTML = unit(sens.exhaust, "°C");
  $("#s-exhaust").textContent = sens.inlet != null && sens.exhaust != null ? `+${fmt(sens.exhaust - sens.inlet)} °C over inlet` : "";
  $("#v-watts").innerHTML = unit(sens.watts, "W");
  const w = s.history.filter(p => p.watts != null && p.t > Date.now() / 1000 - 3600).map(p => p.watts);
  $("#s-watts").textContent = w.length > 1 ? `1 h average: ${fmt(w.reduce((a, b) => a + b) / w.length)} W` : "";

  const hour = s.history.filter(p => p.t > Date.now() / 1000 - 3600);
  spark("#sp-cpu", hour, "cpu", "var(--cool)", 10);
  spark("#sp-speed", hour, monitor ? "fanpct" : "speed", "var(--accent)", 30);
  spark("#sp-rpm", hour, rpms.length ? "rpm" : "fanpct", "var(--ink-2)", rpms.length ? 2000 : 30);
  spark("#sp-inlet", hour, "inlet", "var(--ink-2)", 8);
  spark("#sp-exhaust", hour, "exhaust", "var(--ink-2)", 10);
  spark("#sp-watts", hour, "watts", "var(--ink-2)", 40);

  // bars relative to the fastest the fans went in the stored history, so 30 % looks like 30 %
  const maxRpm = Math.max(...rpms, ...s.history.map(p => p.rpm || 0), 1) * 1.1;
  $("#fan-count").textContent = sens.fans.length ? `${sens.fans.length} fans` : "";
  $("#fan-unit").textContent = rpms.length ? "RPM" : "%";
  $("#fans").innerHTML = sens.fans.map(f => {
    const pct = f.rpm != null ? f.rpm / maxRpm * 100 : f.pct;
    return `<tr><td>${esc(f.name)}${f.ok ? "" : ' <span class="muted">· alert</span>'}</td>
    <td class="bar"><div class="meter"><i style="width:${clamp(pct ?? 0, 0, 100)}%"></i></div></td>
    <td class="r">${f.rpm != null ? f.rpm.toLocaleString() : f.pct}</td></tr>`;
  }).join("") || '<tr class="empty"><td colspan="3">No readings</td></tr>';
  $("#temp-count").textContent = sens.temps.length ? `${sens.temps.length} sensors` : "";
  const pos = v => clamp((v - T_MIN) / (T_MAX - T_MIN) * 100, 2, 100);
  $("#temps").innerHTML = sens.temps.map(t => {
    const near = t.warn && t.value >= t.warn - draft.threshold_margin;
    return `<tr><td>${esc(t.name)} <span class="mono muted">${esc(t.entity)}</span></td>
    <td class="bar"><div class="meter${t.warn ? " has-warn" : ""}"><i class="${near || (t.cpu && t.value >= fs - 5) ? "hot" : "c"}" style="width:${pos(t.value)}%"></i>
      ${t.warn ? `<b class="warn-tick" style="left:${pos(t.warn)}%" title="BMC warning at ${fmt(t.warn)} °C"></b>` : ""}</div></td>
    <td class="r">${fmt(t.value)}${t.warn ? `<small class="muted"> / ${fmt(t.warn)}</small>` : ""}</td></tr>`;
  }).join("") || '<tr class="empty"><td colspan="3">No readings</td></tr>';
  $("#updated").textContent = `every ${s.interval} s`;
  $("#log").innerHTML = s.events.map(e => `<tr><td>${time(e.t, true)}</td><td class="${e.level}">${esc(e.msg)}</td></tr>`).join("")
    || '<tr class="empty"><td>Nothing yet</td></tr>';

  // control panel, or why there is none
  $("#control-body").hidden = monitor;
  $("#monitor-note").hidden = !monitor;
  if (monitor) {
    $("#applied").textContent = "monitoring";
    $("#monitor-note").innerHTML = `<b>Monitoring only.</b> ${esc(cap(s.monitor_reason))}, so the BMC keeps its own fan curve.
      Temperatures, history, Discord alerts and metrics work as usual.`;
  } else {
    $("#applied").textContent = auto ? "BMC in control" : `applying ${s.applied_speed} %`;
    $("#pcie-row").hidden = !s.pcie;
    renderControls();
  }
  $("#legend-auto").hidden = monitor;
  renderChart();
}

function spark(sel, pts, key, color, minSpan) {
  const svg = $(sel), W = svg.clientWidth || 160, H = 30;
  const vals = pts.filter(p => p[key] != null);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (vals.length < 2) { svg.innerHTML = `<line x1="0" x2="${W}" y1="${H - 1}" y2="${H - 1}" stroke="var(--rule)"/>`; return; }
  // scale to the data, but never zoom in so far that sensor jitter looks like a trend
  let lo = Math.min(...vals.map(p => p[key])), hi = Math.max(...vals.map(p => p[key]));
  const mid = (lo + hi) / 2, half = Math.max(hi - lo, minSpan) / 2 * 1.15;
  lo = mid - half; hi = mid + half;
  const now = Date.now() / 1000, x = t => (1 - (now - t) / 3600) * W, y = v => H - 2 - (v - lo) / (hi - lo) * (H - 4);
  let d = "";
  pts.forEach((p, i) => { if (p[key] == null) return; d += (d && pts[i - 1]?.[key] != null ? "L" : "M") + x(p.t).toFixed(1) + "," + y(p[key]).toFixed(1); });
  const last = vals[vals.length - 1];
  svg.innerHTML = `<line x1="0" x2="${W}" y1="${H - 1}" y2="${H - 1}" stroke="var(--rule)"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/>
    <circle cx="${x(last.t)}" cy="${y(last[key])}" r="2.5" fill="${color}"/>`;
}

// ---------------------------------------------------------------- controls
function renderControls() {
  $$(".seg button").forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === draft.mode));
  $$(".panel").forEach(p => p.classList.toggle("on", p.dataset.panel === draft.mode));
  if (document.activeElement !== $("#fixed")) $("#fixed").value = draft.fixed_speed;
  if (document.activeElement !== $("#smart-target")) $("#smart-target").value = draft.smart_target;
  $("#smart-out").innerHTML = `${draft.smart_target}<small> °C</small>`;
  $("#smart-live").textContent = server?.settings.mode === "smart" && server.effective === "manual"
    ? `Now ${server.applied_speed} % · ${server.reason.replace(/^smart: /, "")}` : "";
  $("#fixed-out").innerHTML = `${draft.fixed_speed}<small> %</small>`;
  if (document.activeElement !== $("#failsafe")) $("#failsafe").value = draft.failsafe_temp;
  if (document.activeElement !== $("#ramp")) $("#ramp").value = draft.ramp_down_seconds;
  if (document.activeElement !== $("#min-speed")) $("#min-speed").value = draft.min_speed;
  if (document.activeElement !== $("#exhaust-limit")) $("#exhaust-limit").value = draft.exhaust_limit ?? "";
  $("#margin-text").textContent = draft.threshold_margin;
  $$("#bmc-thr button").forEach(b => b.setAttribute("aria-pressed", b.dataset.thr === String(draft.bmc_thresholds)));
  $$("#dry button").forEach(b => b.setAttribute("aria-pressed", b.dataset.dry === String(draft.dry_run)));
  $$("#quiet-on button").forEach(b => b.setAttribute("aria-pressed", b.dataset.quiet === String(draft.quiet.enabled)));
  $("#quiet-fields").classList.toggle("off", !draft.quiet.enabled);
  for (const [sel, key] of [["#quiet-start", "start"], ["#quiet-end", "end"], ["#quiet-max", "max_speed"]])
    if (document.activeElement !== $(sel)) $(sel).value = draft.quiet[key];
  $$("[data-pcie]").forEach(b => b.setAttribute("aria-pressed", b.dataset.pcie === String(draft.pcie_cooling)));
  $("#save").disabled = $("#discard").disabled = !dirty;
  $("#save-state").textContent = dirty ? "Unsaved changes" : "No changes";
  $("#save-state").className = "state" + (dirty ? " dirty" : "");
  if (drag == null) renderCurve();
}
function touch() { dirty = true; renderControls(); }

$$(".seg button").forEach(b => b.onclick = () => { draft.mode = b.dataset.mode; touch(); });
$("#fixed").oninput = e => { draft.fixed_speed = +e.target.value; touch(); };
$("#smart-target").oninput = e => { draft.smart_target = +e.target.value; touch(); };
$$("[data-preset]").forEach(b => b.onclick = () => { draft.fixed_speed = +b.dataset.preset; $("#fixed").value = draft.fixed_speed; touch(); });
$("#failsafe").oninput = e => { const v = +e.target.value; if (v >= 40 && v <= 100) { draft.failsafe_temp = v; touch(); } };
$("#ramp").oninput = e => { const v = +e.target.value; if (Number.isInteger(v) && v >= 0 && v <= 600) { draft.ramp_down_seconds = v; touch(); } };
$$("[data-pcie]").forEach(b => b.onclick = () => { draft.pcie_cooling = JSON.parse(b.dataset.pcie); touch(); });
$$("[data-thr]").forEach(b => b.onclick = () => { draft.bmc_thresholds = b.dataset.thr === "true"; touch(); });
$$("[data-dry]").forEach(b => b.onclick = () => { draft.dry_run = b.dataset.dry === "true"; touch(); });
$$("[data-quiet]").forEach(b => b.onclick = () => { draft.quiet = { ...draft.quiet, enabled: b.dataset.quiet === "true" }; touch(); });
$("#quiet-start").onchange = e => { if (e.target.value) { draft.quiet = { ...draft.quiet, start: e.target.value }; touch(); } };
$("#quiet-end").onchange = e => { if (e.target.value) { draft.quiet = { ...draft.quiet, end: e.target.value }; touch(); } };
$("#quiet-max").oninput = e => { const v = +e.target.value; if (Number.isInteger(v) && v >= 0 && v <= 100) { draft.quiet = { ...draft.quiet, max_speed: v }; touch(); } };
$("#min-speed").oninput = e => { const v = +e.target.value; if (Number.isInteger(v) && v >= 0 && v <= 60) { draft.min_speed = v; touch(); } };
$("#exhaust-limit").oninput = e => {
  const raw = e.target.value.trim(), v = +raw;
  if (raw === "") { draft.exhaust_limit = null; touch(); } else if (v >= 30 && v <= 90) { draft.exhaust_limit = v; touch(); }
};
$("#discard").onclick = () => { dirty = false; draft = structuredClone(server.settings); renderServer(); };
$("#save").onclick = async () => {
  $("#save").disabled = true;
  try {
    const r = await api("/api/settings?server=" + encodeURIComponent(server.id), draft);
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    dirty = false; draft = body;
    toast("Settings applied");
    renderControls();
    setTimeout(pollServer, 1200);
  } catch (e) {
    toast("Could not apply: " + e.message, true);
    $("#save").disabled = false;
  }
};

// ---------------------------------------------------------------- curve editor
const CW = 400, CH = 230, P = { l: 34, r: 10, t: 12, b: 26 };
const cx = t => P.l + (t - T_MIN) / (T_MAX - T_MIN) * (CW - P.l - P.r);
const cy = v => P.t + (1 - v / 100) * (CH - P.t - P.b);
const ct = x => T_MIN + (x - P.l) / (CW - P.l - P.r) * (T_MAX - T_MIN);
const cs = y => 100 * (1 - (y - P.t) / (CH - P.t - P.b));

function renderCurve() {
  const pts = [...draft.curve].sort((a, b) => a[0] - b[0]), fs = draft.failsafe_temp;
  let g = `<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line y2="5" stroke="var(--accent)" stroke-opacity=".25"/></pattern></defs>`;
  for (let v = 0; v <= 100; v += 25) g += `<line class="g" x1="${P.l}" x2="${CW - P.r}" y1="${cy(v)}" y2="${cy(v)}"/><text x="${P.l - 6}" y="${cy(v) + 3.5}" text-anchor="end">${v}</text>`;
  for (let t = 30; t <= 90; t += 10) g += `<text x="${cx(t)}" y="${CH - 8}" text-anchor="middle">${t}°</text>`;
  g += `<rect x="${cx(fs)}" y="${P.t}" width="${Math.max(0, CW - P.r - cx(fs))}" height="${CH - P.t - P.b}" fill="url(#hatch)"/>
    <line class="fs" x1="${cx(fs)}" x2="${cx(fs)}" y1="${P.t}" y2="${CH - P.b}"/>
    <text class="fs-text" x="${cx(fs) - 4}" y="${P.t + 10}" text-anchor="end">Auto ≥ ${fs}°</text>`;
  const line = [[T_MIN, pts[0][1]], ...pts, [T_MAX, pts[pts.length - 1][1]]].map(([t, v]) => `${cx(t).toFixed(1)},${cy(v).toFixed(1)}`);
  g += `<polygon class="area" points="${cx(T_MIN)},${cy(0)} ${line.join(" ")} ${cx(T_MAX)},${cy(0)}"/><polyline class="line" points="${line.join(" ")}"/>`;
  const now = server?.cpu_temp;
  if (now != null) {
    const sp = curveSpeed(draft.curve, now);
    g += `<line class="now-l" x1="${cx(now)}" x2="${cx(now)}" y1="${cy(sp)}" y2="${CH - P.b}"/>
      <circle class="now" cx="${cx(now)}" cy="${cy(sp)}" r="4"/>
      <text x="${cx(now) + 7}" y="${cy(sp) + 14}" style="fill:var(--accent)">${Math.round(now)}° → ${sp}%</text>`;
  }
  draft.curve.forEach((p, i) => g += `<g class="pt" data-i="${i}"><circle cx="${cx(p[0])}" cy="${cy(p[1])}" r="13" fill="transparent"/><circle cx="${cx(p[0])}" cy="${cy(p[1])}" r="4.5"/></g>`);
  $("#curve").innerHTML = g;
}

function svgPoint(e) {
  const r = $("#curve").getBoundingClientRect();
  return { x: (e.clientX - r.left) / r.width * CW, y: (e.clientY - r.top) / r.height * CH, r };
}
$("#curve").addEventListener("pointerdown", e => {
  const g = e.target.closest(".pt");
  if (!g) return;
  drag = +g.dataset.i;
  $("#curve").setPointerCapture(e.pointerId);
});
$("#curve").addEventListener("pointermove", e => {
  if (drag == null) return;
  const { x, y, r } = svgPoint(e);
  const p = draft.curve[drag] = [Math.round(clamp(ct(x), T_MIN, T_MAX)), Math.round(clamp(cs(y), 0, 100))];
  dirty = true;
  const tip = $("#curve-tip");
  tip.style.display = "block";
  tip.style.left = cx(p[0]) / CW * r.width + "px";
  tip.style.top = cy(p[1]) / CH * r.height + "px";
  tip.textContent = `${p[0]} °C → ${p[1]} %`;
  renderCurve();
});
const endDrag = () => {
  if (drag == null) return;
  drag = null; $("#curve-tip").style.display = "none";
  draft.curve.sort((a, b) => a[0] - b[0]);
  renderControls();
};
$("#curve").addEventListener("pointerup", endDrag);
$("#curve").addEventListener("pointercancel", endDrag);
$("#curve").addEventListener("dblclick", e => {
  const g = e.target.closest(".pt");
  if (g) {
    if (draft.curve.length <= 2) return toast("The curve needs at least 2 points", true);
    draft.curve.splice(+g.dataset.i, 1);
  } else {
    if (draft.curve.length >= 10) return toast("10 points at most", true);
    const { x, y } = svgPoint(e);
    draft.curve.push([Math.round(clamp(ct(x), T_MIN, T_MAX)), Math.round(clamp(cs(y), 0, 100))]);
    draft.curve.sort((a, b) => a[0] - b[0]);
  }
  touch();
});

// ---------------------------------------------------------------- history chart
$$(".tabs button").forEach(b => b.onclick = () => {
  range = +b.dataset.range;
  $$(".tabs button").forEach(x => x.setAttribute("aria-pressed", x === b));
  renderChart();
});

let chart = null;  // geometry of the last render, for the hover tooltip
function renderChart() {
  if (!server) return;
  const monitor = server.effective === "monitor" || !server.control, fanKey = monitor ? "fanpct" : "speed";
  const svg = $("#chart"), W = svg.clientWidth || 800, H = svg.clientHeight || 320;
  const pl = 36, pr = 40, pt = 10, pb = 26;
  const end = Date.now() / 1000, start = end - range;
  const h = server.history.filter(p => p.t >= start);
  const x = t => pl + (t - start) / range * (W - pl - pr);
  const yT = v => pt + (1 - (v - T_MIN) / (T_MAX - T_MIN)) * (H - pt - pb);
  const yS = v => pt + (1 - v / 100) * (H - pt - pb);
  const step = (server.interval || 15) * 1.5;
  let g = `<defs><pattern id="autoband" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line y2="6" stroke="var(--rule-2)"/></pattern></defs>`;
  for (let v = 0; v <= 100; v += 25) g += `<line class="grid-l" x1="${pl}" x2="${W - pr}" y1="${yS(v)}" y2="${yS(v)}"/><text x="${W - pr + 8}" y="${yS(v) + 4}">${v}%</text>`;
  for (let v = 20; v <= 95; v += 15) g += `<text x="${pl - 8}" y="${yT(v) + 4}" text-anchor="end">${v}°</text>`;
  const n = W < 520 ? 3 : 6;
  for (let i = 0; i <= n; i++) {
    const t = start + range * i / n;
    g += `<text x="${x(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === n ? "end" : "middle"}">${time(t)}</text>`;
  }
  if (!monitor) {  // stretches where the BMC had control, as hatched bands
    let band = null;
    const flush = t => { if (band != null) { g += `<rect x="${x(band)}" y="${pt}" width="${Math.max(2, x(t) - x(band))}" height="${H - pt - pb}" fill="url(#autoband)"/>`; band = null; } };
    h.forEach(p => { if (p.speed == null) { if (band == null) band = p.t - step / 3; } else flush(p.t); });
    flush(h.length ? h[h.length - 1].t + step / 3 : end);
  }
  const path = (key, y) => {
    let d = "", pen = false;
    h.forEach((p, i) => {
      if (p[key] == null || (i && p.t - h[i - 1].t > step * 2)) pen = false;
      if (p[key] == null) return;
      d += `${pen ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`; pen = true;
    });
    return d;
  };
  g += `<line class="axis" x1="${pl}" x2="${W - pr}" y1="${H - pb}" y2="${H - pb}"/>
    <path d="${path("exhaust", yT)}" fill="none" stroke="var(--ink-3)" stroke-width="1.25" stroke-dasharray="4 3"/>
    <path d="${path(fanKey, yS)}" fill="none" stroke="var(--accent)" stroke-width="1.75" stroke-linejoin="round"/>
    <path d="${path("cpu", yT)}" fill="none" stroke="var(--cool)" stroke-width="1.75" stroke-linejoin="round"/>
    <g id="cross" style="display:none"><line class="axis" y1="${pt}" y2="${H - pb}"/><circle r="3.5" fill="var(--cool)"/><circle r="3.5" fill="var(--accent)"/></g>`;
  if (!h.length) g += `<text x="${(W + pl - pr) / 2}" y="${H / 2}" text-anchor="middle">Collecting readings…</text>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = g;
  chart = { h, x, yT, yS, W, pl, pr, fanKey };
}

$("#chart").addEventListener("pointermove", e => {
  if (!chart || !chart.h.length) return;
  const r = $("#chart").getBoundingClientRect(), mx = e.clientX - r.left;
  const p = chart.h.reduce((a, b) => Math.abs(chart.x(b.t) - mx) < Math.abs(chart.x(a.t) - mx) ? b : a);
  const X = chart.x(p.t), cross = $("#cross"), [line, c1, c2] = cross.children, fan = p[chart.fanKey];
  cross.style.display = "";
  line.setAttribute("x1", X); line.setAttribute("x2", X);
  c1.setAttribute("cx", X); c1.setAttribute("cy", p.cpu != null ? chart.yT(p.cpu) : -10);
  c2.setAttribute("cx", X); c2.setAttribute("cy", fan != null ? chart.yS(fan) : -10);
  const tip = $("#tip");
  tip.innerHTML = `<div class="t">${time(p.t, true)}</div>
    <div><span>CPU</span><span>${fmt(p.cpu)} °C</span></div>
    <div><span>Exhaust</span><span>${fmt(p.exhaust)} °C</span></div>
    <div><span>Fans</span><span>${fan == null ? "Automatic" : fan + " %"}</span></div>
    ${p.rpm != null ? `<div><span>Fan speed</span><span>${p.rpm.toLocaleString()} rpm</span></div>` : ""}
    <div><span>Power</span><span>${fmt(p.watts)} W</span></div>`;
  tip.style.display = "block";
  tip.style.left = (X > r.width / 2 ? X - tip.offsetWidth - 14 : X + 14) + "px";
});
$("#chart").addEventListener("pointerleave", () => { $("#tip").style.display = "none"; const c = $("#cross"); if (c) c.style.display = "none"; });

// ---------------------------------------------------------------- add / edit a server
const HOST_HELP = {
  dell: "iDRAC IP or host name. \"local\" when this container runs on the server itself.",
  supermicro: "BMC IP or host name. \"local\" when this container runs on the server itself.",
  ipmi: "BMC IP or host name. \"local\" when this container runs on the server itself.",
  redfish: "iLO / BMC IP or host name, optionally with :port.",
  "ilo4-unlocked": "iLO IP or host name. SSH (port 22) and HTTPS must both be reachable.",
};
const NOTES = {
  dell: "Enable <b>IPMI over LAN</b> in the iDRAC (iDRAC Settings → Network → IPMI Settings). The user must be an Administrator. iDRAC 9 firmware 3.34.34.34 and later no longer accept fan commands; those servers can be added as monitoring only with the Redfish type.",
  supermicro: "<b>Experimental.</b> Tested commands for X9, X10 and X11 boards: the BMC is put in Full fan mode and both zones are set. Fans go back to Optimal mode when the controller stops or anything fails.",
  "ilo4-unlocked": "<b>Experimental, and only for unlocked firmware.</b> Requires the community-patched iLO 4 2.77 on a ProLiant Gen8 or Gen9. Stock iLO 4 refuses the commands; add it with the Redfish type instead. Flashing modified firmware is at your own risk.",
  redfish: "Works with HPE iLO 4 (2.30 or later), iLO 5, iLO 6, Lenovo XCC, Dell iDRAC 9 and most recent BMCs. The vendor keeps control of the fans; you get temperatures, fan speeds, power, history, alerts and metrics.",
  ipmi: "Reads every temperature, fan and power sensor the BMC exposes over IPMI. The BMC keeps control of the fans.",
  demo: "Simulated readings, to try everything without hardware. Nothing is sent anywhere.",
};
let editing = null, editDriver = "";

async function openEditor() {
  if (!overview) await pollOverview();
  editing = null; editDriver = route.driver || "";
  $("#e-result").hidden = true;
  $("#e-form").reset();
  if (route.id) {
    const r = await api("/api/servers/" + encodeURIComponent(route.id));
    if (!r.ok) { toast("That server no longer exists", true); location.hash = "#/"; return; }
    editing = await r.json();
    if (editing.source === "environment") { toast("This server is defined in the environment; change it there", true); location.hash = "#/server/" + editing.id; return; }
    editDriver = editing.driver;
    $("#e-name").value = editing.name; $("#e-host").value = editing.host;
    $("#e-user").value = editing.username; $("#e-tls").checked = editing.verify_tls;
  }
  $("#e-title").textContent = editing ? `Edit ${editing.name}` : "Add a server";
  $("#e-crumb").textContent = editing ? editing.name : "Add server";
  $("#e-lede").textContent = editing ? "Change how to reach the management controller. Leave the password empty to keep the saved one."
    : "Choose what you have, enter how to reach its management controller, and test the connection.";
  $("#e-delete").hidden = !editing;
  $("#e-cancel").href = editing ? "#/server/" + encodeURIComponent(editing.id) : "#/";
  renderEditor();
}

function renderEditor() {
  $("#e-tiles").innerHTML = tiles(overview.drivers, editDriver, false);
  const d = overview.drivers.find(x => x.kind === editDriver);
  $("#e-step2").hidden = !d;
  if (!d) return;
  $("#e-note").innerHTML = NOTES[d.kind] || "";
  $("#e-note").className = "note" + (d.experimental ? " warn" : "");
  $("#e-host-row").hidden = $("#e-cred-row").hidden = !d.needs_host;
  $("#e-tls-row").hidden = !["redfish", "ilo4-unlocked"].includes(d.kind);
  $("#e-host-help").textContent = HOST_HELP[d.kind] || "";
  $("#e-user").placeholder = { dell: "root", supermicro: "ADMIN", "ilo4-unlocked": "Administrator", redfish: "Administrator" }[d.kind] || "admin";
  $("#e-pass").placeholder = editing?.has_password ? "Saved. Type to change" : "";
  if (!$("#e-name").value && !editing) $("#e-name").placeholder = d.kind === "demo" ? "Demo server" : "Rack A";
}

$("#e-tiles").addEventListener("click", e => {
  const t = e.target.closest(".tile");
  if (!t) return;
  editDriver = t.dataset.kind;
  $("#e-result").hidden = true;
  renderEditor();
  $("#e-name").focus();
});

function editorBody() {
  const b = { driver: editDriver, name: $("#e-name").value.trim() || $("#e-name").placeholder,
              host: $("#e-host").value.trim(), username: $("#e-user").value.trim(), verify_tls: $("#e-tls").checked };
  if ($("#e-pass").value) b.password = $("#e-pass").value;
  if (editing) b.id = editing.id;
  return b;
}

$("#e-test").onclick = async () => {
  const res = $("#e-result");
  res.hidden = false; res.className = "result"; res.textContent = "Contacting the BMC…";
  $("#e-test").disabled = true;
  try {
    const r = await api("/api/servers/test", editorBody());
    const d = await r.json();
    if (d.ok) {
      res.className = "result ok";
      res.innerHTML = `<b>Connected${d.model ? " to " + esc(d.model) : ""}.</b> ${d.temps} temperature sensors, ${d.fans} fans` +
        (d.cpu != null ? `, CPU at ${fmt(d.cpu)} °C` : "") + (d.watts != null ? `, ${fmt(d.watts)} W` : "") + "." +
        (d.control ? "" : " Monitoring only.");
    } else {
      res.className = "result bad";
      res.innerHTML = `<b>Could not connect.</b> ${esc(d.error)}`;
    }
  } catch {
    res.className = "result bad"; res.textContent = "The dashboard did not answer.";
  }
  $("#e-test").disabled = false;
};

$("#e-form").onsubmit = async e => {
  e.preventDefault();
  $("#e-save").disabled = true;
  const r = await api(editing ? "/api/servers/" + encodeURIComponent(editing.id) : "/api/servers", editorBody());
  const d = await r.json();
  $("#e-save").disabled = false;
  if (!r.ok) return toast(d.error, true);
  toast(editing ? "Server updated" : `${d.name} added`);
  await pollOverview();
  location.hash = "#/server/" + encodeURIComponent(d.id);
};

$("#e-delete").onclick = async () => {
  if (!confirm(`Remove ${editing.name}? Its fans go back to automatic control, and its settings and history are deleted.`)) return;
  const r = await api("/api/servers/" + encodeURIComponent(editing.id) + "/delete", {});
  if (!r.ok) return toast((await r.json()).error, true);
  toast(`${editing.name} removed`);
  await pollOverview();
  location.hash = "#/";
};

// ---------------------------------------------------------------- shell
$("#signout").onclick = async () => {
  await api("/api/logout", {});
  location.replace("/login");
};

addEventListener("resize", () => { if (route.view === "server" && server) renderServer(); });
addEventListener("beforeunload", e => { if (dirty || (typeof aDirty !== "undefined" && aDirty)) e.preventDefault(); });

route = parseRoute();
pollOverview().then(go);
setInterval(pollOverview, 5000);
setInterval(pollServer, 5000);
