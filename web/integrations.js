// Prometheus, Grafana and Homarr pages: generated snippets, live checks and previews.
// Tokens are never sent to the browser, so snippets carry placeholders for them.
let integ = null, grafanaPanels = null;

const METRICS = [
  ["idrac_up", "1 if the last BMC reading succeeded"],
  ["idrac_cpu_temperature_celsius", "Hottest CPU"],
  ["idrac_inlet_temperature_celsius", "Inlet air"],
  ["idrac_exhaust_temperature_celsius", "Exhaust air"],
  ["idrac_temperature_celsius", "Every temperature sensor (+ sensor, entity)"],
  ["idrac_fan_rpm", "Fan speed in RPM (+ fan)"],
  ["idrac_fan_percent", "Fan speed in % as the BMC reports it (+ fan)"],
  ["idrac_fan_speed_percent", "Speed set by this controller"],
  ["idrac_power_watts", "Power draw"],
  ["idrac_power_on", "1 if the server is on"],
  ["idrac_dell_control", "1 while the BMC's own fan control is active"],
  ["idrac_failsafe_active", "1 while the failsafe holds"],
  ["idrac_last_update_timestamp_seconds", "Time of the last reading"],
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
  $("#pm-enable").hidden = on;
  $("#pm-n-scrape").textContent = on ? "1" : "2";
  $("#pm-n-check").textContent = on ? "2" : "3";
  $("#pm-env").textContent = `environment:\n  METRICS_TOKEN: ${randomToken()}`;
  const auth = integ.metrics_open ? "" : `    authorization:\n      credentials: <METRICS_TOKEN>\n`;
  $("#pm-scrape").textContent = `scrape_configs:\n  - job_name: idrac-fan-control\n    scrape_interval: ${Math.max(15, integ.interval)}s\n` +
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

function randomToken() {
  const a = new Uint8Array(24);
  crypto.getRandomValues(a);
  return [...a].map(b => b.toString(16).padStart(2, "0")).join("");
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
function renderHomarr() {
  setStatus("#hm-status", integ.embed_token, integ.embed_token ? "Enabled" : "Off");
  $("#hm-enable").hidden = integ.embed_token;
  $("#hm-n-build").textContent = integ.embed_token ? "1" : "2";
  $("#hm-n-add").textContent = integ.embed_token ? "2" : "3";
  $("#hm-env").textContent = `environment:\n  EMBED_TOKEN: ${randomToken()}`;
  const sel = $("#hm-server"), keep = sel.value;
  sel.innerHTML = integ.servers.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")
    || '<option value="">No servers yet</option>';
  if (integ.servers.some(s => s.id === keep)) sel.value = keep;
  $("#hm-health").textContent = `${location.origin}/healthz`;
  buildWidget();
}

function buildWidget() {
  const q = new URLSearchParams();
  if ($("#hm-server").value) q.set("server", $("#hm-server").value);
  if ($("#hm-theme").value) q.set("theme", $("#hm-theme").value);
  if ($("#hm-bg").value) q.set("bg", $("#hm-bg").value);
  const preview = "/embed?" + q;
  q.set("token", "TOKEN");
  $("#hm-url").textContent = `${location.origin}/embed?${q}`.replace("TOKEN", "<EMBED_TOKEN>");
  if ($("#hm-frame").getAttribute("src") !== preview) $("#hm-frame").src = preview;
  $("#hm-board").className = "board " + ($("#hm-theme").value || "auto");
}
["#hm-server", "#hm-theme", "#hm-bg"].forEach(s => $(s).addEventListener("change", buildWidget));

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
