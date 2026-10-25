#!/usr/bin/env python3
import json
from time import time
from config import run_cmd, command_ok

def read_battery():
    res = run_cmd(["termux-battery-status"], timeout=15)
    if not command_ok(res):
        return None
    try:
        data = json.loads(res.stdout)
        percent = int(data["percentage"])
    except (ValueError, KeyError, TypeError):
        return None
    plugged = str(data.get("plugged", "")).upper() not in ("", "UNPLUGGED")
    temp = data.get("temperature")
    return {"percent": percent, "plugged": plugged,
            "temp": temp if isinstance(temp, (int, float)) else None}

class PowerMonitor:
    POLL_SECONDS = 300
    LOW_LEVELS = (10, 20)

    def __init__(self, notifier):
        self.notifier = notifier
        self.last_poll = -1e12
        self.plugged = None
        self.warned = set()
        self.info = None

    def poll(self, force=False):
        now = time()
        if not force and now - self.last_poll < self.POLL_SECONDS:
            return
        self.last_poll = now
        info = read_battery()
        if info is None:
            return
        self.info = info
        pct, plugged = info["percent"], info["plugged"]

        if self.plugged is not None and plugged != self.plugged:
            if plugged:
                self.notifier.send_limited("power_on", f"🔌 Charger connected (battery {pct}%)", 600)
            else:
                self.notifier.send_limited("power_off",
                                           f"🔌 Charger disconnected, running on battery ({pct}%)", 600)
        self.plugged = plugged

        if plugged or pct > 25:
            self.warned.clear()
            return
        crossed = next((lv for lv in self.LOW_LEVELS if pct <= lv), None)
        if crossed is not None and crossed not in self.warned:
            self.warned.update(lv for lv in self.LOW_LEVELS if lv >= crossed)
            self.notifier.send(f"🔋 Battery low: {pct}% (not charging)")

    def describe(self):
        if not self.info:
            return "Battery: n/a"
        state = "charging" if self.info["plugged"] else "on battery"
        text = f"Battery: {self.info['percent']}% ({state})"
        if self.info["temp"] is not None:
            text += f", {self.info['temp']:.0f}°C"
        return text
