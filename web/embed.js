// Read-only widget for dashboards such as Homarr: /embed?server=<id>&token=<EMBED_TOKEN>&theme=dark&bg=solid
const params = new URLSearchParams(location.search);
const $ = id => document.getElementById(id);
const fmt = v => v == null ? "—" : Math.round(v);
if (["light", "dark"].includes(params.get("theme"))) document.documentElement.dataset.theme = params.get("theme");
if (params.get("bg") === "solid") document.body.classList.add("solid");

const api = new URLSearchParams();
for (const k of ["server", "token"]) if (params.get(k)) api.set(k, params.get(k));

async function poll() {
  try {
    const r = await fetch("/api/state?" + api, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status === 401 ? "Add ?token= with EMBED_TOKEN" : "Unavailable");
    render(await r.json());
  } catch (e) {
    $("dot").className = "dot bad";
    $("mode").textContent = e.message;
  }
}

function render(s) {
  const sens = s.sensors || { fans: [] }, dell = s.effective === "dell";
  const rpms = sens.fans.map(f => f.rpm);
  $("name").textContent = s.name;
  $("dot").className = "dot " + (s.error ? "bad" : s.failsafe ? "warn" : "ok");
  $("mode").textContent = s.error ? "iDRAC error" : s.failsafe ? "Failsafe" : dell ? "Dell automatic" : s.settings.mode === "fixed" ? "Fixed" : "Curve";
  $("cpu").innerHTML = s.cpu_temp == null ? "—" : `${fmt(s.cpu_temp)}<small>°C</small>`;
  $("cpu").classList.toggle("hot", s.cpu_temp != null && s.cpu_temp >= s.settings.failsafe_temp - 5);
  $("fans").innerHTML = dell ? "Auto" : `${fmt(s.applied_speed)}<small>%</small>`;
  $("watts").innerHTML = sens.watts == null ? "—" : `${fmt(sens.watts)}<small>W</small>`;
  $("inlet").textContent = sens.inlet == null ? "—" : `${fmt(sens.inlet)} °C`;
  $("exhaust").textContent = sens.exhaust == null ? "—" : `${fmt(sens.exhaust)} °C`;
  $("rpm").textContent = rpms.length ? `${Math.round(rpms.reduce((a, b) => a + b) / rpms.length).toLocaleString()} rpm` : "";

  // last hour: CPU temperature, with the fan speed underneath
  const svg = $("spark"), W = svg.clientWidth || 300, H = 40, now = Date.now() / 1000;
  const pts = s.history.filter(p => p.t > now - 3600);
  const x = t => (1 - (now - t) / 3600) * W;
  const line = (key, lo, hi) => pts.reduce((d, p, i) => p[key] == null ? d :
    d + (d && pts[i - 1]?.[key] != null ? "L" : "M") + x(p.t).toFixed(1) + "," + (H - 3 - (p[key] - lo) / (hi - lo) * (H - 6)).toFixed(1), "");
  const temps = pts.map(p => p.cpu).filter(v => v != null);
  const lo = Math.min(...temps, 30) - 2, hi = Math.max(...temps, lo + 12) + 2;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `<line x1="0" x2="${W}" y1="${H - 1}" y2="${H - 1}" stroke="var(--rule)"/>
    <path d="${line("speed", 0, 100)}" fill="none" stroke="var(--accent)" stroke-width="1.25" opacity=".8"/>
    <path d="${line("cpu", lo, hi)}" fill="none" stroke="var(--cool)" stroke-width="1.5"/>`;
}

poll();
setInterval(poll, 10000);
