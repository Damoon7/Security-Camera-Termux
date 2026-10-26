#!/usr/bin/env python3
import os
import json
import queue
import ssl
import smtplib
import imaplib
import threading
import base64
import urllib.parse
import urllib.request
from email import message_from_bytes
from email.message import EmailMessage
from email.utils import parseaddr
from glob import glob
from time import sleep, time
import numpy as np
from config import (
    run_cmd, log, TG_ALBUM_TIMEOUT, TG_VIDEO_LIMIT, DC_VIDEO_LIMIT,
    MAX_TG_SAMPLES_NO_VIDEO, MAX_TG_SAMPLES_WITH_VIDEO, remove_if_exists,
    has_telegram, has_ntfy, has_deltachat
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


# --------------------------------------------------------------------------- #
# 1. Telegram transport
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# 2. ntfy transport (Supports Basic Auth & Bearer Token)
# --------------------------------------------------------------------------- #
def _ntfy_headers(args, topic_url=None, priority="default", include_actions=True):
    headers = {
        "X-Tags": "seccam_out",
        "X-Priority": priority,
        "User-Agent": "SecCam-Termux/2.0",
    }
    if getattr(args, "ntfy_user", None) and getattr(args, "ntfy_password", None):
        credentials = f"{args.ntfy_user}:{args.ntfy_password}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {encoded}"
    elif getattr(args, "ntfy_token", None):
        headers["Authorization"] = f"Bearer {args.ntfy_token}"

    if include_actions and topic_url:
        headers["X-Actions"] = (
            f"http, Snap, {topic_url}, method=POST, body=/snap, clear=true; "
            f"http, Status, {topic_url}, method=POST, body=/status, clear=true; "
            f"http, Pause 30m, {topic_url}, method=POST, body=/pause 30, clear=true"
        )
    return headers


def ntfy_send_text(args, text, priority="default", retries=2, timeout=25):
    if not has_ntfy(args):
        return False
    topic_url = f"{args.ntfy_server.rstrip('/')}/{args.ntfy_topic}"
    headers = _ntfy_headers(args, topic_url=topic_url, priority=priority)
    data = text.encode("utf-8", errors="replace")
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(topic_url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception as exc:
            if attempt == retries:
                log.warning("ntfy text send failed: %s", exc)
        sleep(2 * attempt)
    return False


def ntfy_send_file(args, filepath, caption, priority="high", retries=2, timeout=90):
    if not has_ntfy(args):
        return False
    base_url = f"{args.ntfy_server.rstrip('/')}/{args.ntfy_topic}"
    params = urllib.parse.urlencode({
        "message": caption,
        "filename": os.path.basename(filepath),
    })
    url = f"{base_url}?{params}"
    headers = _ntfy_headers(args, topic_url=base_url, priority=priority)
    for attempt in range(1, retries + 1):
        try:
            with open(filepath, "rb") as f:
                body = f.read()
            req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception as exc:
            if attempt == retries:
                log.warning("ntfy file upload failed (%s): %s", os.path.basename(filepath), exc)
        sleep(2 * attempt)
    return False


# --------------------------------------------------------------------------- #
# 3. DeltaChat transport
# --------------------------------------------------------------------------- #
def _infer_hosts(email_addr, explicit_host, prefix):
    if explicit_host:
        return [explicit_host.strip()]
    domain = email_addr.split("@", 1)[-1].strip()
    return [f"{prefix}.{domain}", domain]


def dc_send_message(args, text, attachments=None, retries=2, timeout=60):
    if not has_deltachat(args):
        return False
    msg = EmailMessage()
    msg["From"] = args.dc_email
    msg["To"] = args.dc_to
    msg["Subject"] = "SecCam"
    msg["Chat-Version"] = "1.0"
    msg["X-SecCam-Out"] = "1"
    msg.set_content(text)

    for path in (attachments or []):
        if not path or not os.path.exists(path):
            continue
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".mp4":
                maintype, subtype = "video", "mp4"
            else:
                maintype, subtype = "image", "jpeg"
            with open(path, "rb") as f:
                data = f.read()
            msg.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=os.path.basename(path))
        except Exception as exc:
            log.warning("DeltaChat could not attach %s: %s", path, exc)

    hosts = _infer_hosts(args.dc_email, getattr(args, "dc_smtp_host", None), "smtp")
    ports = [args.dc_smtp_port] if getattr(args, "dc_smtp_port", None) else [465, 587]
    ctx = ssl.create_default_context()

    for attempt in range(1, retries + 1):
        for host in hosts:
            for port in ports:
                try:
                    if port == 465:
                        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as s:
                            s.login(args.dc_email, args.dc_password)
                            s.send_message(msg)
                            return True
                    else:
                        with smtplib.SMTP(host, port, timeout=timeout) as s:
                            s.ehlo()
                            s.starttls(context=ctx)
                            s.ehlo()
                            s.login(args.dc_email, args.dc_password)
                            s.send_message(msg)
                            return True
                except Exception as exc:
                    last_err = exc
        if attempt == retries:
            log.warning("DeltaChat SMTP send failed: %s", locals().get("last_err", "unknown error"))
        sleep(2 * attempt)
    return False


# --------------------------------------------------------------------------- #
# Unified Dispatchers (Telegram + ntfy + DeltaChat)
# --------------------------------------------------------------------------- #
def send_single_photo_all(args, filepath, caption):
    opt_photo = prepare_tg_photo(filepath)
    try:
        if has_telegram(args):
            tg_cfg = {"chatid": args.chatid, "bottoken": args.bottoken}
            token, chat = tg_cfg["bottoken"], tg_cfg["chatid"]
            ok = tg_request(token, "sendPhoto", {"chat_id": chat, "caption": caption},
                            {"photo": opt_photo}, timeout=TG_ALBUM_TIMEOUT)
            if not ok:
                tg_request(token, "sendMessage", {"chat_id": chat, "text": caption + " (photo upload failed)"})
        if has_ntfy(args):
            ok = ntfy_send_file(args, opt_photo, caption, priority="default")
            if not ok:
                ntfy_send_text(args, caption + " (photo upload failed)")
        if has_deltachat(args):
            ok = dc_send_message(args, caption, attachments=[opt_photo], timeout=120)
            if not ok:
                dc_send_message(args, caption + " (photo upload failed)")
    finally:
        if opt_photo.endswith(".tg.jpg"):
            remove_if_exists(opt_photo)
        remove_if_exists(filepath)


def send_event_to_telegram(cfg, folder, video_path, photo_count, upload_photos=None):
    token, chat = cfg["bottoken"], cfg["chatid"]
    caption = f"🚨 External object detected ({os.path.basename(folder)}), {photo_count} photos"
    own_photos = upload_photos is None
    if own_photos:
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
                       {"chat_id": chat, "text": caption + " (photo upload failed)"})

        if video_path and os.path.exists(video_path):
            if os.path.getsize(video_path) <= TG_VIDEO_LIMIT:
                tg_request(token, "sendVideo",
                           {"chat_id": chat, "supports_streaming": "true"},
                           {"video": video_path}, timeout=300)
            else:
                log.warning("Video too large for Telegram, kept locally: %s", video_path)
    finally:
        if own_photos:
            for p in upload_photos:
                if p.endswith(".tg.jpg"):
                    remove_if_exists(p)


def send_event_all(args, folder, video_path, photo_count):
    if not has_any_remote(args):
        return
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
        if has_telegram(args):
            tg_cfg = {"chatid": args.chatid, "bottoken": args.bottoken}
            send_event_to_telegram(tg_cfg, folder, video_path, photo_count, upload_photos=upload_photos)

        if has_ntfy(args):
            if upload_photos:
                mid_photo = upload_photos[len(upload_photos) // 2]
                ok = ntfy_send_file(args, mid_photo, caption, priority="high")
                if not ok:
                    ntfy_send_text(args, caption, priority="high")
            else:
                ntfy_send_text(args, caption, priority="high")

        if has_deltachat(args):
            attachments = list(upload_photos)
            if video_path and os.path.exists(video_path) and os.path.getsize(video_path) <= DC_VIDEO_LIMIT:
                attachments.append(video_path)
            ok = dc_send_message(args, caption, attachments=attachments, timeout=240)
            if not ok:
                dc_send_message(args, caption + " (media upload failed)")
    finally:
        for p in upload_photos:
            if p.endswith(".tg.jpg"):
                remove_if_exists(p)


# --------------------------------------------------------------------------- #
# Status Notifier & Multi-Channel Controller
# --------------------------------------------------------------------------- #
class StatusNotifier:
    def __init__(self, args):
        self.args = args
        self.token = args.bottoken
        self.chat = args.chatid
        self.mode = args.status_alerts
        self.tg_enabled = has_telegram(args)
        self.ntfy_enabled = has_ntfy(args)
        self.dc_enabled = has_deltachat(args)
        self.available = self.tg_enabled or self.ntfy_enabled or self.dc_enabled
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
        cleaned = text
        for secret in (self.token, getattr(self.args, "ntfy_password", None),
                       getattr(self.args, "ntfy_token", None),
                       getattr(self.args, "dc_password", None)):
            if secret:
                cleaned = cleaned.replace(secret, "***")
        return cleaned[:1000]

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

    def _deliver(self, text, markup, retries=2, timeout=30):
        if self.tg_enabled:
            fields = {"chat_id": self.chat, "text": text}
            if markup is not None:
                fields["reply_markup"] = json.dumps(markup)
            tg_request(self.token, "sendMessage", fields, retries=retries, timeout=timeout)
        if self.ntfy_enabled:
            ntfy_send_text(self.args, text, retries=retries, timeout=timeout)
        if self.dc_enabled:
            dc_send_message(self.args, text, retries=retries, timeout=timeout)

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            text, markup = item
            try:
                self._deliver(text, markup, retries=2, timeout=30)
            except Exception:
                log.exception("Status message failed")

    def flush(self, timeout=15):
        if self._thread is not None and self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout)

    def send_now(self, text, level="important", force=False):
        if self._wanted(level, force):
            self._deliver(self._clean(text), None, retries=1, timeout=15)


class TelegramController:
    """Listens for remote commands from Telegram, ntfy, and DeltaChat simultaneously."""

    def __init__(self, args, notifier):
        self.args = args
        self.token = args.bottoken
        self.chat = str(args.chatid) if args.chatid is not None else None
        self.notifier = notifier
        self.commands = queue.Queue()
        self.wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._threads = []

        if has_telegram(args):
            t = threading.Thread(target=self._poll_telegram_loop, daemon=True)
            t.start()
            self._threads.append(t)
        if has_ntfy(args):
            t = threading.Thread(target=self._poll_ntfy_loop, daemon=True)
            t.start()
            self._threads.append(t)
        if has_deltachat(args):
            t = threading.Thread(target=self._poll_deltachat_loop, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop_event.set()
        self.wake_event.set()

    def wait(self, seconds):
        self.wake_event.wait(timeout=max(0.0, seconds))
        self.wake_event.clear()

    def _register_bot_menu(self):
        if not has_telegram(self.args):
            return
        tg_request_raw(self.token, "setMyCommands",
                       {"commands": json.dumps(BOT_COMMANDS_SPEC)}, retries=1, timeout=15)

    def _flush_old_updates(self):
        if not has_telegram(self.args):
            return None
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

    def _handle_incoming_text(self, text):
        text = (text or "").strip()
        if not text:
            return
        first_line = next((ln.strip() for ln in text.splitlines()
                           if ln.strip() and not ln.strip().startswith(">")), "")
        if not first_line:
            return
        mapped = BUTTON_TEXT_MAP.get(first_line.lower())
        if mapped:
            self.commands.put(mapped)
            self.wake_event.set()
        elif first_line.startswith("/"):
            self._enqueue_raw_command(first_line)

    def _poll_telegram_loop(self):
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
                self._handle_incoming_text(msg.get("text"))

    def _poll_ntfy_loop(self):
        server = self.args.ntfy_server.rstrip("/")
        topic = self.args.ntfy_topic
        since_cursor = str(int(time()))
        headers = {"User-Agent": "SecCam-Termux/2.0"}
        if getattr(self.args, "ntfy_user", None) and getattr(self.args, "ntfy_password", None):
            credentials = f"{self.args.ntfy_user}:{self.args.ntfy_password}"
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"
        elif getattr(self.args, "ntfy_token", None):
            headers["Authorization"] = f"Bearer {self.args.ntfy_token}"

        while not self._stop_event.is_set():
            url = f"{server}/{topic}/json?poll=1&since={urllib.parse.quote(since_cursor)}"
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw_lines = resp.read().decode("utf-8", errors="replace").splitlines()
                for line in raw_lines:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get("id"):
                        since_cursor = item["id"]
                    if item.get("event") != "message":
                        continue
                    tags = item.get("tags") or []
                    if "seccam_out" in tags:
                        continue
                    self._handle_incoming_text(item.get("message"))
            except Exception:
                pass
            self._stop_event.wait(timeout=8)

    def _connect_imap(self):
        hosts = _infer_hosts(self.args.dc_email, getattr(self.args, "dc_imap_host", None), "imap")
        port = getattr(self.args, "dc_imap_port", 993) or 993
        ctx = ssl.create_default_context()
        for host in hosts:
            try:
                M = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
                M.login(self.args.dc_email, self.args.dc_password)
                M.select("INBOX")
                return M
            except Exception:
                continue
        return None

    def _extract_email_text(self, msg):
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload is not None:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""

    def _poll_deltachat_loop(self):
        last_uid = None
        allowed_sender = self.args.dc_to.strip().lower()

        while not self._stop_event.is_set():
            M = self._connect_imap()
            if M is None:
                self._stop_event.wait(timeout=20)
                continue
            try:
                typ, data = M.uid("SEARCH", None, "ALL")
                if typ == "OK" and data and data[0]:
                    uids = [int(x) for x in data[0].split() if x.isdigit()]
                else:
                    uids = []

                if last_uid is None:
                    last_uid = max(uids) if uids else 0
                else:
                    new_uids = [u for u in uids if u > last_uid]
                    for u in sorted(new_uids):
                        last_uid = max(last_uid, u)
                        ftyp, fdata = M.uid("FETCH", str(u), "(BODY.PEEK[])")
                        if ftyp != "OK" or not fdata or not isinstance(fdata[0], tuple):
                            continue
                        msg = message_from_bytes(fdata[0][1])
                        if msg.get("X-SecCam-Out") == "1":
                            continue
                        sender = parseaddr(msg.get("From", ""))[1].strip().lower()
                        if sender != allowed_sender:
                            continue
                        body = self._extract_email_text(msg)
                        self._handle_incoming_text(body)
            except Exception:
                pass
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
            self._stop_event.wait(timeout=12)
