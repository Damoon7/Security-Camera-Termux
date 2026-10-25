#!/usr/bin/env python3
import os
import json
import queue
import threading
from glob import glob
from time import sleep, time
import numpy as np
from config import (
    run_cmd, log, TG_ALBUM_TIMEOUT, TG_VIDEO_LIMIT,
    MAX_TG_SAMPLES_NO_VIDEO, MAX_TG_SAMPLES_WITH_VIDEO, remove_if_exists
)
from vision import prepare_tg_photo

HELP_TEXT = (
    "🤖 Control Panel & Commands:\n"
    "Use the buttons below or type:\n"
    "/status - Show camera status & battery\n"
    "/snap - Take & send an instant photo\n"
    "/pause [min] - Pause monitoring (e.g. /pause 20)\n"
    "/resume - Resume monitoring immediately\n"
    "/calibrate - Recalibrate motion sensor now\n"
    "/sensitivity <1-500> - Set sensitivity %\n"
    "/schedule <HH:MM-HH:MM|off> - Set active hours\n"
    "/stop - Safely stop the security camera"
)

BOT_COMMANDS_SPEC = [
    {"command": "status", "description": "📊 Show status, threshold & battery"},
    {"command": "snap", "description": "📸 Take an instant photo"},
    {"command": "pause", "description": "⏸️ Pause monitoring (choose duration)"},
    {"command": "resume", "description": "▶️ Resume monitoring now"},
    {"command": "sensitivity", "description": "🎯 Adjust detection sensitivity"},
    {"command": "schedule", "description": "🕒 Set active hours schedule"},
    {"command": "calibrate", "description": "🔧 Recalibrate sensor baseline"},
    {"command": "stop", "description": "🛑 Stop security camera"},
    {"command": "help", "description": "🎛️ Show control panel & help"},
]

MAIN_REPLY_KEYBOARD = {
    "keyboard": [
        [{"text": "📸 Snap"}, {"text": "📊 Status"}],
        [{"text": "⏸️ Pause"}, {"text": "▶️ Resume"}],
        [{"text": "🎯 Sensitivity"}, {"text": "🕒 Schedule"}],
        [{"text": "🔧 Calibrate"}, {"text": "🛑 Stop"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

BUTTON_TEXT_MAP = {
    "📸 snap": ("/snap", ""),
    "📊 status": ("/status", ""),
    "⏸️ pause": ("/pause_menu", ""),
    "▶️ resume": ("/resume", ""),
    "🎯 sensitivity": ("/sensitivity_menu", ""),
    "🕒 schedule": ("/schedule_menu", ""),
    "🔧 calibrate": ("/calibrate", ""),
    "🛑 stop": ("/stop_ask", ""),
}

PAUSE_INLINE_KB = {
    "inline_keyboard": [
        [
            {"text": "15m", "callback_data": "/pause 15"},
            {"text": "30m", "callback_data": "/pause 30"},
            {"text": "1h", "callback_data": "/pause 60"},
            {"text": "2h", "callback_data": "/pause 120"},
        ],
        [
            {"text": "♾️ Pause Indefinitely", "callback_data": "/pause"},
            {"text": "▶️ Resume Now", "callback_data": "/resume"},
        ],
    ]
}

SENSITIVITY_INLINE_KB = {
    "inline_keyboard": [
        [
            {"text": "50% (Low)", "callback_data": "/sensitivity 50"},
            {"text": "75%", "callback_data": "/sensitivity 75"},
            {"text": "100% (Default)", "callback_data": "/sensitivity 100"},
        ],
        [
            {"text": "125%", "callback_data": "/sensitivity 125"},
            {"text": "150% (High)", "callback_data": "/sensitivity 150"},
        ],
    ]
}

SCHEDULE_INLINE_KB = {
    "inline_keyboard": [
        [
            {"text": "🌙 20:00 - 08:00", "callback_data": "/schedule 20:00-08:00"},
            {"text": "🌙 22:00 - 07:00", "callback_data": "/schedule 22:00-07:00"},
        ],
        [
            {"text": "🏢 08:00 - 17:00", "callback_data": "/schedule 08:00-17:00"},
            {"text": "🟢 24/7 (Always On)", "callback_data": "/schedule off"},
        ],
    ]
}

STOP_CONFIRM_INLINE_KB = {
    "inline_keyboard": [
        [
            {"text": "✅ Yes, Stop Camera", "callback_data": "/stop confirm"},
            {"text": "❌ Cancel", "callback_data": "/cancel_stop"},
        ]
    ]
}


def tg_request_raw(token, method, fields=None, files=None, retries=3, timeout=90):
    for attempt in range(1, retries + 1):
        cmd = ["curl", "-s", "--connect-timeout", "15", "--max-time", str(timeout), "-K", "-"]
        for k, v in (fields or {}).items():
            cmd += ["--form-string", f"{k}={v}"]
        for k, p in (files or {}).items():
            cmd += ["-F", f"{k}=@{p}"]
        cfg = f'url = "https://api.telegram.org/bot{token}/{method}"\n'
        res = run_cmd(cmd, timeout=timeout + 10, input_text=cfg)
        if res and res.stdout:
            try:
                data = json.loads(res.stdout)
            except ValueError:
                data = {}
            if data.get("ok"):
                return data
            code = data.get("error_code")
            if code == 429:
                sleep(min(int(data.get("parameters", {}).get("retry_after", 5)), 60))
                continue
            if code in (400, 401, 403, 404, 409):
                log.error("Telegram %s rejected (%s): %s", method, code, data.get("description"))
                return None
        sleep(2 * attempt)
    log.error("Telegram %s failed after %d attempts", method, retries)
    return None


def tg_request(token, method, fields=None, files=None, retries=3, timeout=90):
    return tg_request_raw(token, method, fields=fields, files=files, retries=retries, timeout=timeout) is not None


def send_single_photo_to_telegram(cfg, filepath, caption):
    token, chat = cfg["bottoken"], cfg["chatid"]
    opt_photo = prepare_tg_photo(filepath)
    try:
        ok = tg_request(token, "sendPhoto", {"chat_id": chat, "caption": caption},
                        {"photo": opt_photo}, timeout=TG_ALBUM_TIMEOUT)
        if not ok:
            tg_request(token, "sendMessage",
                       {"chat_id": chat, "text": caption + " (photo upload failed)"})
    finally:
        if opt_photo.endswith(".tg.jpg"):
            remove_if_exists(opt_photo)
        remove_if_exists(filepath)


def send_event_to_telegram(cfg, folder, video_path, photo_count):
    token, chat = cfg["bottoken"], cfg["chatid"]
    caption = f"🚨 External object detected ({os.path.basename(folder)}), {photo_count} photos"

    raw_photos = sorted(glob(os.path.join(folder, "*.jpeg")))
    max_samples = MAX_TG_SAMPLES_WITH_VIDEO if video_path else MAX_TG_SAMPLES_NO_VIDEO
    if len(raw_photos) > max_samples:
        idx = sorted(set(np.linspace(0, len(raw_photos) - 1, max_samples).round().astype(int)))
        selected_photos = [raw_photos[i] for i in idx]
    else:
        selected_photos = raw_photos

    upload_photos = [prepare_tg_photo(p) for p in selected_photos]

    try:
        if len(upload_photos) == 1:
            ok = tg_request(token, "sendPhoto", {"chat_id": chat, "caption": caption},
                            {"photo": upload_photos[0]}, timeout=TG_ALBUM_TIMEOUT)
        elif len(upload_photos) >= 2:
            media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(upload_photos))]
            media[0]["caption"] = caption
            ok = tg_request(token, "sendMediaGroup",
                            {"chat_id": chat, "media": json.dumps(media)},
                            {f"p{i}": p for i, p in enumerate(upload_photos)},
                            timeout=TG_ALBUM_TIMEOUT)
        else:
            ok = tg_request(token, "sendMessage", {"chat_id": chat, "text": caption})

        if not ok and upload_photos:
            tg_request(token, "sendMessage",
                       {"chat_id": chat, "text": caption + " (photo upload failed, files kept on phone)"})

        if video_path and os.path.exists(video_path):
            if os.path.getsize(video_path) <= TG_VIDEO_LIMIT:
                tg_request(token, "sendVideo",
                           {"chat_id": chat, "supports_streaming": "true"},
                           {"video": video_path}, timeout=300)
            else:
                log.warning("Video too large for Telegram, kept locally: %s", video_path)
    finally:
        for p in upload_photos:
            if p.endswith(".tg.jpg"):
                remove_if_exists(p)


class StatusNotifier:
    def __init__(self, args):
        self.token = args.bottoken
        self.chat = args.chatid
        self.mode = args.status_alerts
        self.available = bool(self.token and self.chat)
        self._last_sent = {}
        self._q = queue.Queue()
        self._thread = None
        if self.available:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _wanted(self, level, force):
        if not self.available:
            return False
        if force:
            return True
        if self.mode == "off":
            return False
        return not (level == "all" and self.mode != "all")

    def _clean(self, text):
        return text.replace(self.token, "***")[:1000]

    def send(self, text, level="important", force=False, reply_markup=None):
        if self._wanted(level, force):
            self._q.put((self._clean(text), reply_markup))

    def reply(self, text, reply_markup=None):
        if reply_markup is None:
            reply_markup = MAIN_REPLY_KEYBOARD
        self.send(text, force=True, reply_markup=reply_markup)

    def send_limited(self, key, text, min_interval=1800, level="important"):
        now = time()
        if now - self._last_sent.get(key, -1e12) < min_interval:
            return
        if self._wanted(level, False):
            self._last_sent[key] = now
            self._q.put((self._clean(text), None))

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            text, markup = item
            try:
                fields = {"chat_id": self.chat, "text": text}
                if markup is not None:
                    fields["reply_markup"] = json.dumps(markup)
                tg_request(self.token, "sendMessage", fields, retries=2, timeout=30)
            except Exception:
                log.exception("Status message failed")

    def flush(self, timeout=15):
        if self._thread is not None and self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout)

    def send_now(self, text, level="important", force=False):
        if self._wanted(level, force):
            tg_request(self.token, "sendMessage", {"chat_id": self.chat, "text": self._clean(text)},
                       retries=1, timeout=15)


class TelegramController:
    def __init__(self, args, notifier):
        self.token = args.bottoken
        self.chat = str(args.chatid) if args.chatid is not None else None
        self.notifier = notifier
        self.commands = queue.Queue()
        self.wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        if self.token and self.chat:
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        self.wake_event.set()

    def wait(self, seconds):
        self.wake_event.wait(timeout=max(0.0, seconds))
        self.wake_event.clear()

    def _register_bot_menu(self):
        tg_request_raw(self.token, "setMyCommands",
                       {"commands": json.dumps(BOT_COMMANDS_SPEC)}, retries=1, timeout=15)

    def _flush_old_updates(self):
        data = tg_request_raw(self.token, "getUpdates", {"offset": "-1", "timeout": "0"},
                              retries=1, timeout=15)
        if data and data.get("result"):
            return data["result"][-1]["update_id"] + 1
        return None

    def _enqueue_raw_command(self, text):
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        self.commands.put((cmd, arg))
        self.wake_event.set()

    def _poll_loop(self):
        self._register_bot_menu()
        offset = self._flush_old_updates()
        fail_streak = 0
        while not self._stop_event.is_set():
            fields = {"timeout": "20", "allowed_updates": json.dumps(["message", "callback_query"])}
            if offset is not None:
                fields["offset"] = str(offset)
            data = tg_request_raw(self.token, "getUpdates", fields=fields, retries=1, timeout=30)
            if not data or not isinstance(data.get("result"), list):
                fail_streak += 1
                backoff = min(60, 3 * (2 ** min(fail_streak - 1, 4)))
                self._stop_event.wait(timeout=backoff)
                continue
            fail_streak = 0
            for upd in data["result"]:
                offset = upd["update_id"] + 1

                cb = upd.get("callback_query")
                if cb:
                    cb_msg = cb.get("message") or {}
                    sender_chat = str((cb_msg.get("chat") or {}).get("id", ""))
                    if sender_chat == self.chat:
                        cb_id = cb.get("id")
                        if cb_id:
                            tg_request_raw(self.token, "answerCallbackQuery",
                                           {"callback_query_id": str(cb_id)}, retries=1, timeout=10)
                        cb_data = (cb.get("data") or "").strip()
                        if cb_data.startswith("/"):
                            self._enqueue_raw_command(cb_data)
                    continue

                msg = upd.get("message") or {}
                sender_chat = str((msg.get("chat") or {}).get("id", ""))
                if sender_chat != self.chat:
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue

                mapped = BUTTON_TEXT_MAP.get(text.lower())
                if mapped:
                    self.commands.put(mapped)
                    self.wake_event.set()
                elif text.startswith("/"):
                    self._enqueue_raw_command(text)
