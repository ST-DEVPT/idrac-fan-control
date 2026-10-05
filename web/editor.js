// Adding and editing a server: the hardware picker, network scan, detection, the connection test.

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
  supermicro: "<b>Experimental.</b> For X10, X11 and X12 boards (X9 uses other commands): the BMC is put in Full fan mode and both zones are set, never below 25 %. When the controller stops or anything fails, the BMC gets back the fan mode it had before.",
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
  $("#d-result").hidden = true;
  $("#d-form").reset();
  $("#sc-range").placeholder = guessRange() || "192.168.1.0/24";
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
  $("#d-form").hidden = $("#sc-form").hidden = !!editing;
  if (editing) $("#sc-results").hidden = true;
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

// a guess at the LAN range, from the address this page was opened on
function guessRange() {
  const m = location.hostname.match(/^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.(\d+)\.(\d+)\.?(\d+)?$/);
  const parts = location.hostname.split(".");
  return m && parts.length === 4 ? `${parts[0]}.${parts[1]}.${parts[2]}.0/24` : "";
}

$("#sc-form").onsubmit = async e => {
  e.preventDefault();
  const range = $("#sc-range").value.trim() || $("#sc-range").placeholder;
  const box = $("#sc-results");
  box.hidden = false;
  box.innerHTML = `<p class="hint">Scanning ${esc(range)}… a /24 takes about 15 seconds.</p>`;
  $("#sc-go").disabled = true;
  try {
    const r = await api("/api/servers/scan", { range });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error);
    box.innerHTML = d.found.length ? `<table class="scan-table"><thead><tr><th>Address</th><th>BMC</th><th>Answers</th><th>Suggested</th><th></th></tr></thead><tbody>` +
      d.found.map(f => {
        const drv = overview.drivers.find(x => x.kind === f.suggested);
        return `<tr><td class="mono">${esc(f.host)}</td>
          <td>${esc([f.vendor, f.product].filter(Boolean).join(" · ") || "Unknown")}${f.firmware ? ` <span class="muted">${esc(f.firmware)}</span>` : ""}</td>
          <td>${[f.redfish && "Redfish", f.ipmi && "IPMI"].filter(Boolean).join(", ")}</td>
          <td>${esc(drv ? drv.label : "—")}</td>
          <td class="r">${f.added ? '<span class="muted">Added</span>' :
            `<button class="inline-btn" data-host="${esc(f.host)}" data-kind="${esc(f.suggested || "")}" data-note="${esc(f.note)}">Use</button>`}</td></tr>`;
      }).join("") + "</tbody></table>"
      : `<p class="hint">No BMC answered in ${esc(range)}. Check the range, and that the container can reach that network.</p>`;
  } catch (err) {
    box.innerHTML = `<p class="result bad">${esc(err.message)}</p>`;
  }
  $("#sc-go").disabled = false;
};
$("#sc-results").addEventListener("click", e => {
  const b = e.target.closest("button[data-host]");
  if (!b) return;
  editDriver = b.dataset.kind;
  $("#e-host").value = b.dataset.host;
  $("#d-host").value = b.dataset.host;
  const res = $("#d-result");
  res.hidden = !b.dataset.note;
  res.className = "result ok";
  res.textContent = b.dataset.note;
  renderEditor();
  $("#e-step2").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#e-name").focus({ preventScroll: true });
});

$("#d-form").onsubmit = async e => {
  e.preventDefault();
  const host = $("#d-host").value.trim(), res = $("#d-result");
  if (!host) return $("#d-host").focus();
  res.hidden = false; res.className = "result"; res.textContent = "Asking the BMC…";
  $("#d-go").disabled = true;
  try {
    const r = await api("/api/servers/detect", { host, username: $("#d-user").value.trim(), password: $("#d-pass").value });
    const d = await r.json();
    if (d.found && d.suggested) {
      res.className = "result ok";
      res.innerHTML = `<b>${esc([d.vendor, d.product].filter(Boolean).join(" · ") || "Found")}</b>` +
        (d.firmware ? `, firmware ${esc(d.firmware)}` : "") + ` (via ${esc(d.via)}). ${esc(d.note)}`;
      editDriver = d.suggested;
      $("#e-host").value = host;
      if ($("#d-user").value) $("#e-user").value = $("#d-user").value.trim();
      if ($("#d-pass").value) $("#e-pass").value = $("#d-pass").value;
      renderEditor();
    } else {
      res.className = "result bad";
      res.textContent = d.error || d.note || "Could not tell what this BMC is.";
    }
  } catch {
    res.className = "result bad"; res.textContent = "The dashboard did not answer.";
  }
  $("#d-go").disabled = false;
};

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
        (d.cpu != null ? `, CPU at ${fmt(tv(d.cpu))} ${tu()}` : "") + (d.watts != null ? `, ${fmt(d.watts)} W` : "") + "." +
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
