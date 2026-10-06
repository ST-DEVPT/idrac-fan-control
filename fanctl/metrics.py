"""Prometheus metrics: every reading of every server, in the text exposition format."""

from .alerts import SENT
from .config import VERSION


def metrics(servers):
    def esc(v):
        return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    out = {}

    def add(name, help_, labels, value, kind="gauge"):
        if value is None:
            return
        rows = out.setdefault(name, [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"])
        lab = ",".join(f'{k}="{esc(v)}"' for k, v in labels.items())
        rows.append(f"{name}{{{lab}}} {value:.15g}")

    add("fanctl_info", "Build information.", {"version": VERSION}, 1)
    add("fanctl_alerts_sent_total", "Alerts handed to Discord, ntfy, Gotify or a webhook.", {}, SENT["sent"], "counter")
    add("fanctl_alerts_failed_total", "Alerts a channel did not accept.", {}, SENT["failed"], "counter")
    for s in list(servers.values()):
        with s.lock:
            st = dict(s.state)
        sens = st["sensors"] or {}
        lbl = {"server": s.id, "name": s.name, "driver": s.driver.kind}
        add("fanctl_up", "1 if the last BMC reading succeeded.", lbl, 0 if st["error"] or not st["updated"] else 1)
        add("fanctl_last_update_timestamp_seconds", "Unix time of the last reading.", lbl, st["updated"])
        add("fanctl_power_on", "1 if the server is powered on.", lbl,
            None if st["power"] is None else int(st["power"] == "on"))
        add("fanctl_bmc_control", "1 if the BMC's own fan control is active.", lbl,
            None if st["effective"] is None else int(st["effective"] != "manual"))
        add("fanctl_failsafe_active", "1 while the failsafe temperature has handed control to the BMC.",
            lbl, int(st["failsafe"]))
        add("fanctl_fan_speed_percent", "Fan speed set by the controller (absent in automatic mode).", lbl, st["applied_speed"])
        add("fanctl_cpu_temperature_celsius", "Hottest CPU temperature.", lbl, st["cpu_temp"])
        add("fanctl_inlet_temperature_celsius", "Inlet air temperature.", lbl, sens.get("inlet"))
        add("fanctl_exhaust_temperature_celsius", "Exhaust air temperature.", lbl, sens.get("exhaust"))
        add("fanctl_power_watts", "System power draw.", lbl, sens.get("watts"))
        add("fanctl_loop_last_tick_timestamp_seconds", "Unix time the control loop last turned; stalls show here.",
            lbl, s.tick)
        for key, help_ in (("bmc_errors", "Failed BMC readings."), ("fan_commands", "Fan commands the BMC accepted."),
                           ("fan_commands_refused", "Fan commands the BMC refused."),
                           ("failsafe_trips", "Times the failsafe handed the fans to the BMC.")):
            add(f"fanctl_{key}_total", help_, lbl, s.counters[key], "counter")
        for fan in sorted(s.fans_failed):
            add("fanctl_fan_failed", "1 for a fan that stopped while the others spin.", {**lbl, "fan": fan}, 1)
        sm = st.get("smart")
        if sm and "sensor" in sm:  # smart mode in control: what it sees and why it chose that speed
            add("fanctl_smart_target_celsius", "Temperature smart mode aims at, for the sensor it follows.",
                {**lbl, "sensor": sm["sensor"]}, sm["target"])
            add("fanctl_smart_predicted_celsius", "Where that temperature is heading, 40 s on.",
                {**lbl, "sensor": sm["sensor"]}, sm["predicted"])
            add("fanctl_smart_trend_celsius_per_minute", "Trend of that temperature.", lbl, sm["trend"])
            add("fanctl_smart_learned_speed_percent", "Speed the learned map gives for the current load.", lbl, sm["learned"])
            add("fanctl_smart_trim_percent", "Correction on top of the learned map.", lbl, sm["trim"])
            add("fanctl_smart_boost", "1 while smart mode boosts the fans ahead of a trip point.", lbl, int(sm["boost"]))
            add("fanctl_smart_map_points", "Load levels smart mode has learned.", lbl, sm["points"])
        for t in sens.get("temps", []):
            add("fanctl_temperature_celsius", "Temperature sensor reading.",
                {**lbl, "sensor": t["name"], "entity": t["entity"]}, t["value"])
        for f in sens.get("fans", []):
            add("fanctl_fan_rpm", "Fan speed in RPM.", {**lbl, "fan": f["name"]}, f["rpm"])
            add("fanctl_fan_percent", "Fan speed in percent, as reported by the BMC.", {**lbl, "fan": f["name"]}, f["pct"])
    return "\n".join(row for rows in out.values() for row in rows) + "\n"
