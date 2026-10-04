const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const T_MIN = 20, T_MAX = 95;

let server = null, draft = null, dirty = false, range = 3600, drag = null;
let sid = new URLSearchParams(location.hash.slice(1)).get("server") || "";
const q = () => sid ? `?server=${encodeURIComponent(sid)}` : "";

const fmt = (v, d = 0) => v == null ? "—" : Number(v).toFixed(d);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const time = (t, sec) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(sec && { second: "2-digit" }) });
const unit = (v, u, d = 0) => v == null ? "—" : `${fmt(v, d)}<small>${u}</small>`;
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";

function toast(msg, error) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (error ? " error" : "");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.className = "toast", 3000);
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

// ---------------------------------------------------------------- data
async function poll() {
  try {
    const r = await fetch("/api/state" + q(), { cache: "no-store" });
    if (r.status === 401) return location.replace("/login");
    if (r.status === 404 && sid) { sid = ""; history.replaceState(null, "", location.pathname); return poll(); }
    if (!r.ok) throw new Error(r.status);
    server = await r.json();
    sid = server.id;
    if (!dirty) draft = structuredClone(server.settings);
    render();
  } catch {
    $("#dot-link").className = "dot bad";
    $("#link").textContent = "Dashboard offline";
  }
}

function render() {
  const s = server, sens = s.sensors || { temps: [], fans: [] };
  const dell = s.effective === "dell";
  const rpms = sens.fans.map(f => f.rpm);
  const avg = rpms.length ? rpms.reduce((a, b) => a + b) / rpms.length : null;

  // top bar
  $("#model").textContent = s.model || "Dell server";
  $("#signout").hidden = !s.auth;
  $("#version").textContent = s.version;
  document.title = `${s.name} · iDRAC Fan Control`;
  $("#servers").hidden = s.servers.length < 2;
  $("#server-tabs").innerHTML = s.servers.map(x => `<button class="srv" data-id="${esc(x.id)}" aria-current="${x.id === s.id}">
    <span class="dot ${x.error ? "bad" : x.failsafe ? "warn" : x.effective ? "ok" : ""}"></span>${esc(x.name)}
    <span class="t">${x.cpu_temp == null ? "—" : fmt(x.cpu_temp) + "°"} · ${x.effective === "dell" ? "auto" : x.applied_speed == null ? "—" : x.applied_speed + "%"}</span></button>`).join("");
  $("#host").textContent = s.host === "local" ? "iDRAC local" : s.host === "demo" ? "simulated data" : `iDRAC ${s.host}`;
  const stale = !s.updated || Date.now() / 1000 - s.updated > s.interval * 3;
  $("#dot-link").className = "dot " + (s.error ? "bad" : stale ? "warn" : "ok");
  $("#link").textContent = s.error ? "iDRAC error" : stale ? "Waiting for readings" : `Read ${Math.max(0, Math.round(Date.now() / 1000 - s.updated))} s ago`;
  $("#dot-power").className = "dot " + (s.power === "on" ? "ok" : s.power === "off" ? "warn" : "");
  $("#power").textContent = s.power === "on" ? "Powered on" : s.power === "off" ? "Powered off" : "Power —";
  $("#mode-now").textContent = dell ? "Dell automatic" : `${s.settings.mode === "fixed" ? "Fixed" : "Curve"} · ${s.applied_speed} %`;

  // alert
  const alert = s.error ? ["iDRAC", s.error] : s.failsafe ? ["Failsafe", `${cap(s.reason)}. The iDRAC is controlling the fans.`] : null;
  $("#alert").classList.toggle("show", !!alert);
  if (alert) { $("#alert-title").textContent = alert[0]; $("#alert-msg").textContent = alert[1]; }

  // readouts
  const cpus = sens.temps.filter(t => t.cpu).sort((a, b) => b.value - a.value);
  const fs = draft.failsafe_temp;
  $("#v-cpu").innerHTML = unit(s.cpu_temp, "°C");
  $("#v-cpu").classList.toggle("hot", s.cpu_temp != null && s.cpu_temp >= fs - 5);
  $("#cpu-name").textContent = cpus.length > 1 ? cpus.map(c => `${c.name.replace("CPU ", "#")} ${fmt(c.value)}°`).join(" · ") : "";
  $("#s-cpu").textContent = s.cpu_temp == null ? "" : `${fmt(fs - s.cpu_temp)} °C below failsafe`;
  $("#v-speed").innerHTML = dell ? "Auto" : unit(s.applied_speed, "%");
  $("#s-speed").textContent = cap(s.reason);
  $("#v-rpm").innerHTML = avg == null ? "—" : `${(avg / 1000).toFixed(1)}<small>k rpm</small>`;
  $("#s-rpm").textContent = rpms.length ? `${Math.min(...rpms).toLocaleString()}–${Math.max(...rpms).toLocaleString()} across ${rpms.length} fans` : "";
  $("#v-inlet").innerHTML = unit(sens.inlet, "°C");
  $("#v-exhaust").innerHTML = unit(sens.exhaust, "°C");
  $("#s-exhaust").textContent = sens.inlet != null && sens.exhaust != null ? `+${fmt(sens.exhaust - sens.inlet)} °C over inlet` : "";
  $("#v-watts").innerHTML = unit(sens.watts, "W");
  const w = s.history.filter(p => p.watts != null && p.t > Date.now() / 1000 - 3600).map(p => p.watts);
  $("#s-watts").textContent = w.length > 1 ? `1 h average: ${fmt(w.reduce((a, b) => a + b) / w.length)} W` : "";

  const hour = s.history.filter(p => p.t > Date.now() / 1000 - 3600);
  spark("#sp-cpu", hour, "cpu", "var(--cool)", 10);
  spark("#sp-speed", hour, "speed", "var(--accent)", 30);
  spark("#sp-rpm", hour, "rpm", "var(--ink-2)", 2000);
  spark("#sp-inlet", hour, "inlet", "var(--ink-2)", 8);
  spark("#sp-exhaust", hour, "exhaust", "var(--ink-2)", 10);
  spark("#sp-watts", hour, "watts", "var(--ink-2)", 40);

  // tables
  // bars relative to the fastest the fans went in the stored history, so 30 % looks like 30 %
  const maxRpm = Math.max(...rpms, ...s.history.map(p => p.rpm || 0), 1) * 1.1;
  $("#fan-count").textContent = rpms.length ? `${rpms.length} fans` : "";
  $("#fans").innerHTML = sens.fans.map(f => `<tr><td>${esc(f.name)}${f.ok ? "" : ' <span class="muted">· alert</span>'}</td>
    <td class="bar"><div class="meter"><i style="width:${f.rpm / maxRpm * 100}%"></i></div></td>
    <td class="r">${f.rpm.toLocaleString()}</td></tr>`).join("") || '<tr class="empty"><td colspan="3">No readings</td></tr>';
  $("#temp-count").textContent = sens.temps.length ? `${sens.temps.length} sensors` : "";
  $("#temps").innerHTML = sens.temps.map(t => `<tr><td>${esc(t.name)} <span class="mono muted">${esc(t.entity)}</span></td>
    <td class="bar"><div class="meter"><i class="${t.value >= fs - 5 ? "hot" : "c"}" style="width:${clamp((t.value - T_MIN) / (T_MAX - T_MIN) * 100, 2, 100)}%"></i></div></td>
    <td class="r">${fmt(t.value)}</td></tr>`).join("") || '<tr class="empty"><td colspan="3">No readings</td></tr>';
  $("#updated").textContent = `every ${s.interval} s`;
  $("#log").innerHTML = s.events.map(e => `<tr><td>${time(e.t, true)}</td><td class="${e.level}">${esc(e.msg)}</td></tr>`).join("")
    || '<tr class="empty"><td>Nothing yet</td></tr>';
  $("#applied").textContent = dell ? "iDRAC in control" : `applying ${s.applied_speed} %`;

  renderControls();
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
  $("#fixed-out").innerHTML = `${draft.fixed_speed}<small> %</small>`;
  if (document.activeElement !== $("#failsafe")) $("#failsafe").value = draft.failsafe_temp;
  if (document.activeElement !== $("#ramp")) $("#ramp").value = draft.ramp_down_seconds;
  $$(".mini button").forEach(b => b.setAttribute("aria-pressed", b.dataset.pcie === String(draft.pcie_cooling)));
  $("#save").disabled = $("#discard").disabled = !dirty;
  $("#save-state").textContent = dirty ? "Unsaved changes" : "No changes";
  $("#save-state").className = "state" + (dirty ? " dirty" : "");
  if (drag == null) renderCurve();
}
function touch() { dirty = true; renderControls(); }

$$(".seg button").forEach(b => b.onclick = () => { draft.mode = b.dataset.mode; touch(); });
$("#fixed").oninput = e => { draft.fixed_speed = +e.target.value; touch(); };
$$("[data-preset]").forEach(b => b.onclick = () => { draft.fixed_speed = +b.dataset.preset; $("#fixed").value = draft.fixed_speed; touch(); });
$("#failsafe").oninput = e => { const v = +e.target.value; if (v >= 40 && v <= 100) { draft.failsafe_temp = v; touch(); } };
$("#ramp").oninput = e => { const v = +e.target.value; if (Number.isInteger(v) && v >= 0 && v <= 600) { draft.ramp_down_seconds = v; touch(); } };
$("#server-tabs").onclick = e => {
  const b = e.target.closest(".srv");
  if (!b || b.dataset.id === sid) return;
  if (dirty && !confirm("Discard unsaved changes?")) return;
  sid = b.dataset.id; dirty = false; draft = null;
  history.replaceState(null, "", "#server=" + encodeURIComponent(sid));
  poll();
};
$$(".mini button").forEach(b => b.onclick = () => { draft.pcie_cooling = JSON.parse(b.dataset.pcie); touch(); });
$("#discard").onclick = () => { dirty = false; draft = structuredClone(server.settings); render(); };
$("#save").onclick = async () => {
  $("#save").disabled = true;
  try {
    const r = await fetch("/api/settings" + q(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft) });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    dirty = false; draft = body;
    toast("Settings applied");
    renderControls();
    setTimeout(poll, 1200);
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
    <text class="fs-text" x="${cx(fs) - 4}" y="${P.t + 10}" text-anchor="end">Dell ≥ ${fs}°</text>`;
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
  const svg = $("#chart"), W = svg.clientWidth || 800, H = svg.clientHeight || 320;
  const pl = 36, pr = 40, pt = 10, pb = 26;
  const end = Date.now() / 1000, start = end - range;
  const h = server.history.filter(p => p.t >= start);
  const x = t => pl + (t - start) / range * (W - pl - pr);
  const yT = v => pt + (1 - (v - T_MIN) / (T_MAX - T_MIN)) * (H - pt - pb);
  const yS = v => pt + (1 - v / 100) * (H - pt - pb);
  const step = (server.interval || 15) * 1.5;
  let g = `<defs><pattern id="dell" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line y2="6" stroke="var(--rule-2)"/></pattern></defs>`;
  for (let v = 0; v <= 100; v += 25) g += `<line class="grid-l" x1="${pl}" x2="${W - pr}" y1="${yS(v)}" y2="${yS(v)}"/><text x="${W - pr + 8}" y="${yS(v) + 4}">${v}%</text>`;
  for (let v = 20; v <= 95; v += 15) g += `<text x="${pl - 8}" y="${yT(v) + 4}" text-anchor="end">${v}°</text>`;
  const n = W < 520 ? 3 : 6;
  for (let i = 0; i <= n; i++) {
    const t = start + range * i / n;
    g += `<text x="${x(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === n ? "end" : "middle"}">${time(t)}</text>`;
  }
  // Dell-controlled stretches as hatched bands
  let band = null;
  const flush = t => { if (band != null) { g += `<rect x="${x(band)}" y="${pt}" width="${Math.max(2, x(t) - x(band))}" height="${H - pt - pb}" fill="url(#dell)"/>`; band = null; } };
  h.forEach(p => { if (p.speed == null) { if (band == null) band = p.t - step / 3; } else flush(p.t); });
  flush(h.length ? h[h.length - 1].t + step / 3 : end);
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
    <path d="${path("speed", yS)}" fill="none" stroke="var(--accent)" stroke-width="1.75" stroke-linejoin="round"/>
    <path d="${path("cpu", yT)}" fill="none" stroke="var(--cool)" stroke-width="1.75" stroke-linejoin="round"/>
    <g id="cross" style="display:none"><line class="axis" y1="${pt}" y2="${H - pb}"/><circle r="3.5" fill="var(--cool)"/><circle r="3.5" fill="var(--accent)"/></g>`;
  if (!h.length) g += `<text x="${(W + pl - pr) / 2}" y="${H / 2}" text-anchor="middle">Collecting readings…</text>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = g;
  chart = { h, x, yT, yS, W, pl, pr };
}

$("#chart").addEventListener("pointermove", e => {
  if (!chart || !chart.h.length) return;
  const r = $("#chart").getBoundingClientRect(), mx = e.clientX - r.left;
  const p = chart.h.reduce((a, b) => Math.abs(chart.x(b.t) - mx) < Math.abs(chart.x(a.t) - mx) ? b : a);
  const X = chart.x(p.t), cross = $("#cross"), [line, c1, c2] = cross.children;
  cross.style.display = "";
  line.setAttribute("x1", X); line.setAttribute("x2", X);
  c1.setAttribute("cx", X); c1.setAttribute("cy", p.cpu != null ? chart.yT(p.cpu) : -10);
  c2.setAttribute("cx", X); c2.setAttribute("cy", p.speed != null ? chart.yS(p.speed) : -10);
  const tip = $("#tip");
  tip.innerHTML = `<div class="t">${time(p.t, true)}</div>
    <div><span>CPU</span><span>${fmt(p.cpu)} °C</span></div>
    <div><span>Exhaust</span><span>${fmt(p.exhaust)} °C</span></div>
    <div><span>Fans</span><span>${p.speed == null ? "Dell" : p.speed + " %"}</span></div>
    <div><span>Fan speed</span><span>${p.rpm == null ? "—" : p.rpm.toLocaleString()} rpm</span></div>
    <div><span>Power</span><span>${fmt(p.watts)} W</span></div>`;
  tip.style.display = "block";
  tip.style.left = (X > r.width / 2 ? X - tip.offsetWidth - 14 : X + 14) + "px";
});
$("#chart").addEventListener("pointerleave", () => { $("#tip").style.display = "none"; const c = $("#cross"); if (c) c.style.display = "none"; });

$("#signout").onclick = async () => {
  await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  location.replace("/login");
};

addEventListener("resize", () => server && render());
addEventListener("hashchange", () => {
  const next = new URLSearchParams(location.hash.slice(1)).get("server");
  if (next && next !== sid) { sid = next; dirty = false; draft = null; poll(); }
});
addEventListener("beforeunload", e => { if (dirty || (typeof aDirty !== "undefined" && aDirty)) e.preventDefault(); });
poll();
setInterval(poll, 5000);
