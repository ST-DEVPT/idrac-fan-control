// Prometheus, Grafana and Homarr pages: generated snippets, live checks and previews.
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
const WIDGET_LABELS = { status: "Status and mode", cpu: "CPU temperature", fans: "Fan speed", power: "Power draw",
  inlet: "Inlet air", exhaust: "Exhaust air", chart: "Last hour chart", model: "Server model" };

function renderHomarr() {
  setStatus("#hm-status", integ.embed_token, integ.embed_token ? "Enabled" : "Off");
  $("#hm-enable").hidden = integ.embed_token;
  $$(".hm-n").filter(n => !n.closest("[hidden]")).forEach((n, i) => n.textContent = i + 1);
  $("#hm-env").textContent = `environment:\n  EMBED_TOKEN: ${randomToken()}`;
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
  $("#hm-url").textContent = `${location.origin}/embed?${q}`.replace("TOKEN", "<EMBED_TOKEN>");
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
