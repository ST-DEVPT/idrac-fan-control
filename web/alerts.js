// Discord alert settings: edited as a draft, previewed live, tested before saving.
let alerts = null, aDraft = null, aDirty = false, aKind = "failsafe", aField = "message";
let newWebhook;  // undefined: keep the stored one; "": remove it; otherwise the URL to save
let newSecrets = {};  // channel -> {url?, token?}: only what was typed, so stored secrets stay unless replaced

const KIND_NAMES = {
  failsafe: "Failsafe reached", failsafe_cleared: "Failsafe cleared", hot: "Running hot",
  unreachable: "BMC unreachable", refused: "Fan command refused", recovered: "Back to normal",
  controller_error: "Controller error", failsafe_long: "Failsafe lasting", fan_failed: "Fan failed",
  ignored: "Fan commands ignored", inlet_hot: "Room running hot", settings_changed: "Settings changed", started: "Controller started",
  report: "Status report",
};
const LEVEL_NAMES = { error: "Error", warn: "Warning", ok: "Resolved", info: "Info" };
const SAMPLE = {
  server: "Rack A", host: "192.168.1.120", model: "PowerEdge R730", cpu: "71", speed: "45%", mode: "curve",
  reason: "curve at 71°C", error: "Unable to establish IPMI v2 / RMCP+ session", failsafe: "75",
  interval: "15", time: "12:00:00", period: "1 h", cpu_min: "48", cpu_avg: "55", cpu_max: "71",
  speed_avg: "24%", power_avg: "152", dell_pct: "0", inlet: "36", inlet_threshold: "35", minutes: "10", fan: "Fan3", rpm: "0 rpm",
};
const fillIn = (tpl, v) => tpl.replace(/\{(\w+)\}/g, (m, k) => k in v ? String(v[k]) : m);

async function loadAlerts() {
  const r = await fetch("/api/alerts", { cache: "no-store" });
  if (!r.ok) return;
  alerts = await r.json();
  aDraft = structuredClone(alerts);
  newWebhook = undefined; newSecrets = {}; aDirty = false;
  $("#al-webhook").value = "";
  $$("#channels input[type=password]").forEach(i => i.value = "");
  renderAlerts();
}

function body() {
  const keys = ["enabled", "username", "avatar_url", "footer", "details", "mention", "mention_levels",
                "cooldown_minutes", "hot_threshold", "inlet_threshold", "failsafe_minutes", "report_minutes", "report_mode", "colors", "events"];
  const b = Object.fromEntries(keys.map(k => [k, aDraft[k]]));
  if (newWebhook !== undefined) b.webhook_url = newWebhook;
  b.channels = Object.fromEntries(Object.entries(aDraft.channels).map(([c, ch]) => [c, { enabled: ch.enabled, ...newSecrets[c] }]));
  return b;
}

function aTouch() { aDirty = true; renderAlerts(); }

function setIfIdle(sel, value, prop = "value") {
  const el = $(sel);
  if (document.activeElement !== el) el[prop] = value;
}

function renderAlerts() {
  renderChannels();
  const d = aDraft, w = alerts.webhook;
  const removing = newWebhook === "", replacing = !!newWebhook;
  $("#al-status").textContent = removing ? "Webhook will be removed"
    : replacing ? "New webhook, not saved yet"
    : w.set ? `Webhook ${w.hint} · ${w.source === "environment" ? "from DISCORD_WEBHOOK_URL" : "saved here"}` : "No webhook yet";
  $("#al-clear").hidden = !(w.source === "dashboard" && !removing);
  $("#al-webhook").placeholder = w.set && !removing ? `Saved (${w.hint}). Paste a new URL to replace it` : "https://discord.com/api/webhooks/…";
  $("#al-webhook-note").textContent = w.source === "environment"
    ? "Set by DISCORD_WEBHOOK_URL. A URL saved here takes precedence."
    : "Server Settings → Integrations → Webhooks → Copy Webhook URL";

  $("#al-enabled").checked = d.enabled;
  setIfIdle("#al-username", d.username);
  setIfIdle("#al-footer", d.footer);
  setIfIdle("#al-avatar", d.avatar_url);
  $("#al-details").checked = d.details;
  const [mKind, mId = ""] = d.mention.split(":");
  setIfIdle("#al-mention", mKind);
  setIfIdle("#al-mention-id", mId);
  $("#al-mention-id").disabled = !["role", "user"].includes(mKind);
  setIfIdle("#al-cooldown", d.cooldown_minutes);
  setIfIdle("#al-hot", Math.round(tv(d.hot_threshold)));
  setIfIdle("#al-inlet", Math.round(tv(d.inlet_threshold)));
  setIfIdle("#al-fs-min", d.failsafe_minutes);
  $$(".al-tu").forEach(el => el.textContent = tu());

  $("#al-colors").innerHTML = Object.keys(LEVEL_NAMES).map(l =>
    `<label class="color"><input type="color" data-level="${l}" value="${esc(d.colors[l])}"><span>${LEVEL_NAMES[l]}</span></label>`).join("");
  $("#al-levels").innerHTML = Object.keys(LEVEL_NAMES).map(l =>
    `<label class="check"><input type="checkbox" data-level="${l}" ${d.mention_levels.includes(l) ? "checked" : ""}> ${LEVEL_NAMES[l]}</label>`).join("");

  $("#al-events").innerHTML = Object.keys(KIND_NAMES).map(k => {
    const lvl = alerts.kinds[k];
    return `<li><input type="checkbox" data-kind="${k}" aria-label="Send ${esc(KIND_NAMES[k])}" ${d.events[k].enabled ? "checked" : ""}>
      <button type="button" data-kind="${k}" aria-current="${k === aKind}">${esc(KIND_NAMES[k])}</button>
      <span class="lvl" style="--c:${esc(d.colors[lvl])}">${LEVEL_NAMES[lvl]}</span></li>`;
  }).join("");
  setIfIdle("#al-title", d.events[aKind].title);
  setIfIdle("#al-message", d.events[aKind].message);
  $("#al-report-opts").hidden = aKind !== "report";
  setIfIdle("#al-report-min", d.report_minutes);
  setIfIdle("#al-report-mode", d.report_mode);
  const phs = aKind === "report" ? [...alerts.placeholders, ...alerts.report_placeholders] : alerts.placeholders;
  $("#al-chips").innerHTML = phs.map(p => `<button type="button" data-ph="${p}">{${p}}</button>`).join("");

  $("#al-save").disabled = $("#al-discard").disabled = !aDirty;
  $("#al-test").disabled = removing || !(w.set || replacing);
  $("#al-state").textContent = aDirty ? "Unsaved changes" : "No changes";
  $("#al-state").className = "state" + (aDirty ? " dirty" : "");
  renderPreview();
}

function renderPreview() {
  const d = aDraft, lvl = alerts.kinds[aKind], ev = d.events[aKind];
  const v = { ...SAMPLE, threshold: d.hot_threshold, inlet_threshold: d.inlet_threshold, minutes: d.failsafe_minutes, period: d.report_minutes % 60 ? `${d.report_minutes} min` : `${d.report_minutes / 60} h` };
  if (aKind === "report") return renderReportPreview(d, v);
  const [mKind, mId] = d.mention.split(":");
  const mention = d.mention && d.mention_levels.includes(lvl)
    ? { here: "@here", everyone: "@everyone", role: "@role", user: "@user" }[mKind] + (mId ? ` (${mId})` : "") : "";
  const avatar = /^https:\/\//.test(d.avatar_url) ? `<img class="dc-av" src="${esc(d.avatar_url)}" alt="">`
    : `<img class="dc-av" src="/static/icon.svg" alt="">`;
  const fields = d.details ? `<div class="dc-fields">
      <div><b>CPU</b>${esc(v.cpu)}°C</div><div><b>Fans</b>${esc(v.speed)}</div><div><b>Mode</b>${esc(v.mode)}</div></div>` : "";
  $("#al-preview").innerHTML = `${avatar}<div class="dc-body">
    <div class="dc-head"><b>${esc(d.username)}</b><span class="dc-app">APP</span><span class="dc-time">Today at 12:00</span></div>
    ${mention ? `<div class="dc-mention">${esc(mention)}</div>` : ""}
    <div class="dc-embed" style="--bar:${esc(d.colors[lvl])}">
      <div class="dc-title">${esc(fillIn(ev.title, v))}</div>
      <div class="dc-desc">${esc(fillIn(ev.message, v))}</div>
      ${fields}
      ${d.footer ? `<div class="dc-foot">${esc(fillIn(d.footer, v))} · Today at 12:00</div>` : ""}
    </div>
    ${d.enabled && d.events[aKind].enabled ? "" : '<div class="dc-off">This alert is turned off.</div>'}
  </div>`;
}

function renderReportPreview(d, v) {
  const ev = d.events.report;
  const avatar = /^https:\/\//.test(d.avatar_url) ? esc(d.avatar_url) : "/static/icon.svg";
  const f = (name, value, wide) => `<div class="${wide ? "wide" : ""}"><b>${name}</b>${value}</div>`;
  $("#al-preview").innerHTML = `<img class="dc-av" src="${avatar}" alt=""><div class="dc-body">
    <div class="dc-head"><b>${esc(d.username)}</b><span class="dc-app">APP</span><span class="dc-time">Today at 12:00${d.report_mode === "edit" ? " (edited)" : ""}</span></div>
    <div class="dc-embed" style="--bar:${esc(d.colors.info)}">
      <div class="dc-title">${esc(fillIn(ev.title, v))}</div>
      <div class="dc-desc">${esc(fillIn(ev.message, v))}</div>
      <div class="dc-fields">
        ${f("CPU", "<strong>50°C</strong> now<br>48–71°C · avg 55°C")}
        ${f("Fans", "<strong>20%</strong><br>avg 24% · automatic 0% of the time")}
        ${f("Power", "<strong>151 W</strong> now<br>avg 152 W")}
        ${f("Air", "in 22°C · out 38°C")}
        ${f("Status", "🟢 ok · mode curve")}
        ${f(`CPU, last ${esc(v.period)}`, "<code>▂▂▃▄▅▇██▇▆▅▄▃▃▂▂▂▃▃▄▄▃▂▂</code> 48→71°C", true)}
        ${f(`Fans, last ${esc(v.period)}`, "<code>▁▁▂▂▃▅▇▇▆▅▄▃▂▂▁▁▁▂▂▂▂▂▁▁</code> 15→45%", true)}
        ${f("Recent events", "<code>11:42</code> Fans → 45% (curve at 71°C)", true)}
      </div>
      ${d.footer ? `<div class="dc-foot">${esc(fillIn(d.footer, v))} · Today at 12:00</div>` : ""}
    </div>
    ${d.enabled && ev.enabled ? "" : '<div class="dc-off">Status reports are turned off.</div>'}
  </div>`;
}

// ---------------------------------------------------------------- inputs
const bindText = (sel, key, map = v => v) => $(sel).addEventListener("input", e => { aDraft[key] = map(e.target.value); aTouch(); });
bindText("#al-username", "username");
bindText("#al-footer", "footer");
bindText("#al-avatar", "avatar_url", v => v.trim());
$("#al-cooldown").addEventListener("input", e => { const n = +e.target.value; if (Number.isInteger(n) && n >= 0 && n <= 1440) { aDraft.cooldown_minutes = n; aTouch(); } });
$("#al-report-min").addEventListener("input", e => { const n = +e.target.value; if (Number.isInteger(n) && n >= 5 && n <= 1440) { aDraft.report_minutes = n; aTouch(); } });
$("#al-report-mode").onchange = e => { aDraft.report_mode = e.target.value; aTouch(); };
$("#al-hot").addEventListener("input", e => { const n = fromT(+e.target.value); if (n >= 30 && n <= 100) { aDraft.hot_threshold = n; aTouch(); } });
$("#al-inlet").addEventListener("input", e => { const n = fromT(+e.target.value); if (n >= 15 && n <= 60) { aDraft.inlet_threshold = n; aTouch(); } });
$("#al-fs-min").addEventListener("input", e => { const n = +e.target.value; if (Number.isInteger(n) && n >= 1 && n <= 1440) { aDraft.failsafe_minutes = n; aTouch(); } });
$("#al-enabled").onchange = e => { aDraft.enabled = e.target.checked; aTouch(); };
$("#al-details").onchange = e => { aDraft.details = e.target.checked; aTouch(); };
$("#al-webhook").addEventListener("input", e => { const v = e.target.value.trim(); newWebhook = v || undefined; aTouch(); });
$("#al-clear").onclick = () => { newWebhook = ""; $("#al-webhook").value = ""; aTouch(); };

const setMention = () => {
  const k = $("#al-mention").value, id = $("#al-mention-id").value.trim();
  aDraft.mention = ["role", "user"].includes(k) ? `${k}:${id}` : k;
  aTouch();
};
$("#al-mention").onchange = setMention;
$("#al-mention-id").addEventListener("input", setMention);

$("#al-colors").addEventListener("input", e => { if (e.target.dataset.level) { aDraft.colors[e.target.dataset.level] = e.target.value; aTouch(); } });
$("#al-levels").addEventListener("change", e => {
  const l = e.target.dataset.level;
  aDraft.mention_levels = e.target.checked ? [...new Set([...aDraft.mention_levels, l])] : aDraft.mention_levels.filter(x => x !== l);
  aTouch();
});
$("#al-events").addEventListener("change", e => { const k = e.target.dataset.kind; if (k) { aDraft.events[k].enabled = e.target.checked; aTouch(); } });
$("#al-events").addEventListener("click", e => {
  const b = e.target.closest("button[data-kind]");
  if (b) { aKind = b.dataset.kind; $("#al-title").value = aDraft.events[aKind].title; $("#al-message").value = aDraft.events[aKind].message; renderAlerts(); }
});
for (const f of ["title", "message"]) {
  $("#al-" + f).addEventListener("focus", () => aField = f);
  $("#al-" + f).addEventListener("input", e => { aDraft.events[aKind][f] = e.target.value; aTouch(); });
}
$("#al-chips").addEventListener("click", e => {
  const ph = e.target.dataset.ph;
  if (!ph) return;
  const el = $("#al-" + aField), at = el.selectionStart ?? el.value.length;
  el.value = el.value.slice(0, at) + `{${ph}}` + el.value.slice(el.selectionEnd ?? at);
  el.focus();
  el.selectionStart = el.selectionEnd = at + ph.length + 2;
  aDraft.events[aKind][aField] = el.value;
  aTouch();
});
$("#al-reset").onclick = async () => {
  const r = await fetch("/api/alerts", { cache: "no-store" });  // defaults are not in the draft; ask the server
  const fresh = await r.json();
  const def = fresh.defaults[aKind];
  if (def.title) { aDraft.events[aKind].title = def.title; aDraft.events[aKind].message = def.message; }
  $("#al-title").value = aDraft.events[aKind].title; $("#al-message").value = aDraft.events[aKind].message;
  aTouch();
};

// ---------------------------------------------------------------- actions
const postJSON = (url, data) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

$("#al-save").onclick = async () => {
  $("#al-save").disabled = true;
  const r = await postJSON("/api/alerts", body());
  const res = await r.json();
  if (!r.ok) { toast("Could not save: " + res.error, true); $("#al-save").disabled = false; return; }
  alerts = res; aDraft = structuredClone(res); newWebhook = undefined; newSecrets = {}; aDirty = false;
  $("#al-webhook").value = "";
  $$("#channels input[type=password]").forEach(i => i.value = "");
  renderAlerts();
  toast("Alert settings saved");
};
$("#al-discard").onclick = () => loadAlerts();
$("#al-test").onclick = async () => {
  $("#al-test").disabled = true;
  const r = await postJSON("/api/test-alert", { kind: aKind, config: body() });
  toast(r.ok ? `Test "${KIND_NAMES[aKind]}" sent to Discord${aKind === "report" ? " with the current readings" : ""}` : "Test failed: " + (await r.json()).error, !r.ok);
  $("#al-test").disabled = false;
};

// ---------------------------------------------------------------- ntfy, Gotify, webhook
function renderChannels() {
  for (const box of $$("#channels .ch")) {
    const c = box.dataset.ch, ch = aDraft.channels[c], saved = alerts.channels[c];
    box.querySelector("[data-f=enabled]").checked = ch.enabled;
    const url = box.querySelector("[data-f=url]"), token = box.querySelector("[data-f=token]");
    if (saved.url_set && !url.value) url.placeholder = `${translate("Saved")} (${saved.url_hint})`;
    if (token && saved.token_set && !token.value) token.placeholder = translate("Saved. Type to replace");
    box.classList.toggle("off", !ch.enabled);
  }
}
$("#channels").addEventListener("change", e => {
  const box = e.target.closest(".ch");
  if (e.target.dataset.f === "enabled") { aDraft.channels[box.dataset.ch].enabled = e.target.checked; aTouch(); }
});
$("#channels").addEventListener("input", e => {
  const box = e.target.closest(".ch"), f = e.target.dataset.f;
  if (f === "url" || f === "token") {
    newSecrets[box.dataset.ch] = { ...newSecrets[box.dataset.ch], [f]: e.target.value.trim() };
    aTouch();
  }
});
$("#channels").addEventListener("click", async e => {
  const b = e.target.closest("[data-test]");
  if (!b) return;
  const c = b.closest(".ch").dataset.ch, kind = aKind === "report" ? "failsafe" : aKind;
  b.disabled = true;
  const r = await postJSON("/api/test-alert", { kind, channel: c, config: body() });
  toast(r.ok ? `Test "${KIND_NAMES[kind]}" sent to ${c}` : "Test failed: " + (await r.json()).error, !r.ok);
  b.disabled = false;
});

loadAlerts();
