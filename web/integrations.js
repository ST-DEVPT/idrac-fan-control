// Prometheus, Grafana, Homarr and Backup pages: generated snippets, live checks and previews.
// Tokens are never sent to the browser, so snippets carry placeholders for them.
let integ = null, grafanaPanels = null;

const METRICS = [
  ["fanctl_up", "1 if the last BMC reading succeeded"],
  ["fanctl_cpu_temperature_celsius", "Hottest CPU"],
  ["fanctl_inlet_temperature_celsius", "Inlet air"],
  ["fanctl_exhaust_temperature_celsius", "Exhaust air"],
  ["fanctl_temperature_celsius", "Every temperature sensor (+ sensor, entity)"],
  ["fanctl_fan_rpm", "Fan speed in RPM (+ fan)"],
  ["fanctl_fan_percent", "Fan speed in % as the BMC reports it (+ fan)"],
  ["fanctl_fan_speed_percent", "Speed set by this controller"],
  ["fanctl_power_watts", "Power draw"],
  ["fanctl_power_on", "1 if the server is on"],
  ["fanctl_bmc_control", "1 while the BMC's own fan control is active"],
  ["fanctl_failsafe_active", "1 while the failsafe holds"],
  ["fanctl_last_update_timestamp_seconds", "Time of the last reading"],
  ["fanctl_loop_last_tick_timestamp_seconds", "Time the control loop last turned"],
  ["fanctl_bmc_errors_total", "Failed BMC readings (counter)"],
  ["fanctl_fan_commands_total", "Fan commands the BMC accepted (counter)"],
  ["fanctl_fan_commands_refused_total", "Fan commands the BMC refused (counter)"],
  ["fanctl_failsafe_trips_total", "Times the failsafe took over (counter)"],
  ["fanctl_fan_failed", "1 for a failed fan (+ fan)"],
  ["fanctl_alerts_sent_total", "Alerts sent, all channels (counter)"],
  ["fanctl_smart_target_celsius", "Smart mode: target of the sensor it follows (+ sensor)"],
  ["fanctl_smart_predicted_celsius", "Smart mode: where that temperature is heading (+ sensor)"],
  ["fanctl_smart_learned_speed_percent", "Smart mode: speed learned for the current load"],
  ["fanctl_smart_trim_percent", "Smart mode: correction on top of the learned speed"],
  ["fanctl_smart_boost", "Smart mode: 1 while boosting ahead of a trip point"],
];

async function loadIntegrations() {
  const r = await api("/api/integrations");
  if (r.status === 401) return location.replace("/login");
  integ = await r.json();
}

function setStatus(sel, on, text) {
  const el = $(sel);
  el.className = "status-pill " + (on ? "on" : "off");
  el.textContent = text;
}

async function renderIntegration(view) {
  await loadIntegrations();
  if (view === "prometheus") renderPrometheus();
  if (view === "grafana") renderGrafana();
  if (view === "homarr") renderHomarr();
}

// ---------------------------------------------------------------- Prometheus
function renderPrometheus() {
  const on = integ.metrics_token || integ.metrics_open;
  setStatus("#pm-status", on, integ.metrics_token ? "Enabled · token" : integ.metrics_open ? "Enabled · open" : "Off");
  renderTokens("metrics");
  const auth = integ.metrics_open ? "" : `    authorization:\n      credentials: <your metrics token>\n`;
  $("#pm-scrape").textContent = `scrape_configs:\n  - job_name: fan-control\n    scrape_interval: ${Math.max(15, integ.interval)}s\n` +
    (location.protocol === "https:" ? "    scheme: https\n" : "") + auth +
    `    static_configs:\n      - targets: ["${location.host}"]`;
  $("#pm-interval").textContent = integ.interval;
  $("#pm-metrics").innerHTML = METRICS.map(([n, d]) => `<tr><td><code>${n}</code></td><td>${esc(d)}</td></tr>`).join("");
  refreshMetrics();
}

let metricsText = "";
async function refreshMetrics() {
  const r = await api("/api/metrics-preview");
  metricsText = r.ok ? await r.text() : "Could not read the metrics.";
  showMetrics();
}
function showMetrics() {
  const f = $("#pm-filter").value.trim().toLowerCase();
  const lines = metricsText.split("\n").filter(l => l && !l.startsWith("#") && (!f || l.toLowerCase().includes(f)));
  $("#pm-out").textContent = lines.join("\n") || (f ? "No series match." : "No data yet.");
}
$("#pm-filter").addEventListener("input", showMetrics);
$("#pm-refresh").onclick = refreshMetrics;

// ---------------------------------------------------------------- sessions
$("#sign-out-all").onclick = async () => {
  if (!confirm(translate("Sign out every other browser and phone?"))) return;
  const r = await api("/api/sessions/revoke-all", {});
  toast(r.ok ? "Every other session is signed out" : "Could not sign the others out", !r.ok);
};

// ---------------------------------------------------------------- tokens
// Created here, stored as a hash, shown once. EMBED_TOKEN / METRICS_TOKEN from the environment are listed too.
const fresh = {};  // kind -> the token just created, until the page changes
function renderTokens(kind) {
  const box = $(`.tokens[data-kind="${kind}"]`), admin = integ.role === "admin";
  const rows = integ.tokens.filter(t => t.kind === kind);
  const when = t => t ? new Date(t * 1000).toLocaleDateString() : translate("never");
  box.innerHTML = `
    ${admin ? `<form class="tok-new"><input name="name" maxlength="40" placeholder="${translate(kind === "widget" ? "Name, e.g. Homarr" : "Name, e.g. Prometheus")}"
      aria-label="${translate("Token name")}" required><button class="btn">${translate("Create")}</button></form>` : ""}
    ${fresh[kind] ? `<div class="code"><pre id="tok-${kind}">${esc(fresh[kind])}</pre><button class="copy" data-copy="tok-${kind}">Copy</button></div>
      <p class="hint">${translate("Copy it now: only a hash of it is kept, so it is not shown again.")}</p>` : ""}
    ${rows.length || integ.env_tokens[kind] ? `<table class="tok-list"><tbody>
      ${integ.env_tokens[kind] ? `<tr><td><b>${kind === "widget" ? "EMBED_TOKEN" : "METRICS_TOKEN"}</b></td><td class="muted">${translate("from the environment")}</td><td></td></tr>` : ""}
      ${rows.map(t => `<tr><td><b>${esc(t.name)}</b></td><td class="muted">${translate("created")} ${when(t.created)} · ${translate("last used")} ${when(t.used)}</td>
        <td class="r">${admin ? `<button class="inline-btn" data-revoke="${esc(t.id)}" data-name="${esc(t.name)}">${translate("Revoke")}</button>` : ""}</td></tr>`).join("")}
    </tbody></table>` : `<p class="hint">${translate("No token yet.")}</p>`}`;
  const form = box.querySelector("form");
  if (form) form.onsubmit = async e => {
    e.preventDefault();
    const r = await api("/api/tokens", { name: form.name.value, kind });
    const d = await r.json();
    if (!r.ok) return toast(d.error, true);
    fresh[kind] = d.token;
    await renderIntegration(route.view);
  };
  box.querySelectorAll("[data-revoke]").forEach(b => b.onclick = async () => {
    if (!confirm(translate(`Revoke ${b.dataset.name}? Whatever uses it stops working.`))) return;
    const r = await api("/api/tokens/revoke", { id: b.dataset.revoke });
    if (!r.ok) return toast((await r.json()).error, true);
    toast("Token revoked");
    await renderIntegration(route.view);
  });
}

// ---------------------------------------------------------------- Grafana
async function renderGrafana() {
  $("#gf-servers").innerHTML = integ.servers.length
    ? integ.servers.map(s => `<code>${esc(s.id)}</code>`).join(", ") : "none yet";
  if (!grafanaPanels) {
    const r = await api("/static/grafana.json");
    grafanaPanels = r.ok ? (await r.json()).panels : [];
  }
  const kind = { stat: "Number", timeseries: "Chart" };
  $("#gf-panels").innerHTML = grafanaPanels.map(p => `<div class="panel-chip ${p.type}">
    <span>${esc(p.title)}</span><i>${kind[p.type] || p.type}</i></div>`).join("");
}

// ---------------------------------------------------------------- Homarr
const WIDGET_LABELS = { status: "Status and mode", cpu: "CPU temperature", fans: "Fan speed", power: "Power draw",
  inlet: "Inlet air", exhaust: "Exhaust air", chart: "Last hour chart", model: "Server model" };

function renderHomarr() {
  setStatus("#hm-status", integ.embed_token, integ.embed_token ? "Enabled" : "Off");
  renderTokens("widget");
  const options = integ.servers.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
  for (const sel of ["#hm-server", "#hm-cw-server"]) {
    const keep = $(sel).value;
    $(sel).innerHTML = '<option value="all">All servers</option>' + options;
    $(sel).value = [...$(sel).options].some(o => o.value === keep) ? keep : integ.servers[0]?.id || "all";
  }
  if (!$("#hm-base").value) $("#hm-base").value = location.origin;
  if (!$("#hm-scope").dataset.set) $("#hm-scope").value = /^(localhost|127\.|\[::1\])/.test(location.hostname) ? "loopback" : "private";
  $("#hm-fields").innerHTML = integ.widget_all.map(f => `<label><input type="checkbox" data-field="${f}"
    ${integ.widget_fields.includes(f) ? "checked" : ""}> ${translate(WIDGET_LABELS[f] || f)}</label>`).join("");
  const keepShow = new Set($$("#hm-show input:not(:checked)").map(i => i.dataset.show));
  $("#hm-show").innerHTML = integ.widget_fields.length ? integ.widget_fields.map(f => `<label><input type="checkbox" data-show="${f}"
    ${keepShow.has(f) ? "" : "checked"}> ${translate(WIDGET_LABELS[f] || f)}</label>`).join("") : `<span class="hint">${translate("Nothing is shared yet.")}</span>`;
  $("#hm-health").textContent = `${location.origin}/healthz`;
  buildWidget();
}

function buildWidget() {
  const q = new URLSearchParams({ server: $("#hm-server").value || "all" });
  if ($("#hm-theme").value) q.set("theme", $("#hm-theme").value);
  if ($("#hm-bg").value) q.set("bg", $("#hm-bg").value);
  const shown = $$("#hm-show input:checked").map(i => i.dataset.show);
  if (shown.length < $$("#hm-show input").length) q.set("show", shown.join(","));
  const preview = "/embed?" + q;
  q.set("token", "TOKEN");
  $("#hm-url").textContent = `${location.origin}/embed?${q}`.replace("TOKEN", fresh.widget || "<your widget token>");
  if ($("#hm-frame").getAttribute("src") !== preview) $("#hm-frame").src = preview;
  $("#hm-board").className = "board " + ($("#hm-theme").value || "auto");
  const d = new URLSearchParams({ base: $("#hm-base").value.trim().replace(/\/+$/, ""), server: $("#hm-cw-server").value || "all",
    scope: $("#hm-scope").value });
  $("#hm-download").href = "/api/homarr-widget?" + d;
}
["#hm-server", "#hm-theme", "#hm-bg", "#hm-cw-server", "#hm-base"].forEach(s => $(s).addEventListener("input", buildWidget));
$("#hm-scope").addEventListener("change", () => { $("#hm-scope").dataset.set = "1"; buildWidget(); });
$("#hm-show").addEventListener("change", buildWidget);
$("#hm-download").addEventListener("click", async e => {
  // check the address first, so a typo shows here and not as an unreadable download
  e.preventDefault();
  const r = await api($("#hm-download").getAttribute("href"));
  if (!r.ok) return toast((await r.json().catch(() => ({}))).error || "Could not build the widget", true);
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: "fan-control-homarr-widget.json" });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("#hm-fields").addEventListener("change", async () => {
  const fields = $$("#hm-fields input:checked").map(i => i.dataset.field);
  const r = await api("/api/widget-config", { fields });
  if (!r.ok) return toast((await r.json().catch(() => ({}))).error || "Could not save", true);
  integ.widget_fields = (await r.json()).fields;
  toast("Widgets updated");
  renderHomarr();
  $("#hm-frame").contentWindow?.location.reload();
});

// ---------------------------------------------------------------- copy buttons
document.addEventListener("click", async e => {
  const b = e.target.closest("button.copy");
  if (!b) return;
  const text = document.getElementById(b.dataset.copy).textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch {  // plain-HTTP pages have no clipboard API
    const t = document.createElement("textarea");
    t.value = text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
  }
  b.textContent = "Copied";
  setTimeout(() => b.textContent = "Copy", 1500);
});

// ---------------------------------------------------------------- backup
let backup = null;
$("#bk-secrets").onchange = e => {
  $("#bk-export").href = "/api/export" + (e.target.checked ? "?secrets=1" : "");
  $("#bk-warn").hidden = !e.target.checked;
};
$("#bk-file").onchange = async e => {
  const res = $("#bk-preview"), file = e.target.files[0];
  backup = null; $("#bk-import").disabled = true;
  if (!file) return;
  res.hidden = false;
  try {
    const d = JSON.parse(await file.text());
    if (d.format !== "fan-control-backup") throw new Error("not a Fan Control backup");
    backup = d;
    res.className = "result ok";
    res.textContent = `Backup from ${new Date(d.exported * 1000).toLocaleString()}: ${d.servers.length} server(s), ` +
      `settings for ${Object.keys(d.settings || {}).length}, Discord ${d.alerts ? "included" : "not included"}` +
      (d.with_secrets ? ", with passwords." : ", without passwords (servers will need theirs typed in).");
    $("#bk-import").disabled = false;
  } catch (err) {
    res.className = "result bad"; res.textContent = "Can't use this file: " + err.message;
  }
};
$("#bk-import").onclick = async () => {
  $("#bk-import").disabled = true;
  const r = await api("/api/import", { data: backup });
  const d = await r.json();
  const res = $("#bk-preview");
  if (!r.ok) { res.className = "result bad"; res.textContent = d.error; return; }
  res.className = "result ok";
  res.innerHTML = [d.added.length && `Added: ${esc(d.added.join(", "))}.`, d.updated.length && `Updated: ${esc(d.updated.join(", "))}.`,
    d.skipped.length && `Skipped: ${esc(d.skipped.join("; "))}.`].filter(Boolean).join(" ") || "Nothing to change.";
  pollOverview();
};
