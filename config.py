#!/usr/bin/env python3
import os
import re
import sys
import math
import shutil
import logging
from argparse import ArgumentParser
from datetime import datetime
from logging.handlers import RotatingFileHandler
from subprocess import run, PIPE, TimeoutExpired

log = logging.getLogger("seccam")

EVENTS_DIR = "events"
TMP_PHOTO = "photo_tmp.jpeg"
TRIGGER_PHOTO = "photo_trigger.jpeg"
SNAP_PHOTO = "photo_snap.jpeg"
PHOTO_TIMEOUT = 40
MAX_PHOTOS_PER_EVENT = 300
MAX_TG_SAMPLES_NO_VIDEO = 6
MAX_TG_SAMPLES_WITH_VIDEO = 4
TG_ALBUM_TIMEOUT = 240
TG_VIDEO_LIMIT = 49 * 1024 * 1024
DC_VIDEO_LIMIT = 20 * 1024 * 1024

REQUIRED_TERMUX_BINARIES = (
    "termux-camera-photo",
    "termux-microphone-record",
    "termux-wake-lock",
    "termux-wake-unlock",
)


def setup_logging(path):
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(ch)


def run_cmd(cmd, timeout=30, input_text=None):
    try:
        return run(cmd, input=input_text, stdout=PIPE, stderr=PIPE,
                   text=True, errors="replace", timeout=timeout)
    except TimeoutExpired:
        log.warning("Timeout (%ss): %s", timeout, cmd[0])
    except Exception as exc:
        log.warning("Command failed (%s): %s", cmd[0], exc)
    return None


def remove_if_exists(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def command_ok(res):
    return res is not None and res.returncode == 0


def describe_failure(res):
    if res is None:
        return "command did not run or timed out"
    return (res.stderr or "").strip()[-200:] or f"exit code {res.returncode}"


def require_curl():
    if shutil.which("curl") is None:
        raise RuntimeError("Telegram needs curl but it is not installed. Run: pkg install curl -y")


def has_telegram(args):
    return bool(getattr(args, "chatid", None) and getattr(args, "bottoken", None))


def has_ntfy(args):
    return bool(getattr(args, "ntfy_topic", None))


def has_deltachat(args):
    return bool(getattr(args, "dc_email", None)
                and getattr(args, "dc_password", None)
                and getattr(args, "dc_to", None))


def has_any_remote(args):
    return has_telegram(args) or has_ntfy(args) or has_deltachat(args)


def parse_schedule(spec):
    if not spec or spec.strip().lower() in ("off", "none", "all", "24/7"):
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec)
    if not m:
        raise ValueError("Schedule must be HH:MM-HH:MM in 24h format (e.g. 20:00-08:00)")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59):
        raise ValueError("Invalid hour or minute in schedule (hours 0-23, minutes 0-59)")
    return h1 * 60 + m1, h2 * 60 + m2


def format_schedule(sched):
    if sched is None:
        return "24/7 (always active)"
    s, e = sched
    return f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}"


def is_within_schedule(sched, now_dt=None):
    if sched is None:
        return True
    s, e = sched
    if s == e:
        return True
    if now_dt is None:
        now_dt = datetime.now()
    cur = now_dt.hour * 60 + now_dt.minute
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def validate_args(args):
    if not re.fullmatch(r"\d+", args.camera):
        sys.exit("Error: --camera must be a number (0 or 1)")
    for name in ("sms", "call"):
        val = getattr(args, name)
        if val is not None and not re.fullmatch(r"\+?\d{3,15}", val):
            sys.exit(f"Error: --{name} must be a phone number (digits, optional leading +)")
    if args.chatid is not None and not re.fullmatch(r"-?\d+", args.chatid):
        sys.exit("Error: chat id must be numeric")
    if args.bottoken is not None and not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", args.bottoken):
        sys.exit("Error: bot token format looks wrong")
    if (args.chatid is None) != (args.bottoken is None):
        sys.exit("Error: both chat id and bot token are required for Telegram")

    if args.ntfy_topic is not None:
        args.ntfy_topic = args.ntfy_topic.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", args.ntfy_topic):
            sys.exit("Error: --ntfy-topic must be 3-128 characters (letters, digits, _ or -)")
    if args.ntfy_server:
        args.ntfy_server = args.ntfy_server.strip().rstrip("/")
        if not args.ntfy_server.startswith(("http://", "https://")):
            sys.exit("Error: --ntfy-server must start with http:// or https://")

    dc_fields = (args.dc_email, args.dc_password, args.dc_to)
    if any(dc_fields) and not all(dc_fields):
        sys.exit("Error: --dc-email, --dc-password, and --dc-to are all required for DeltaChat")
    if all(dc_fields):
        for label, addr in (("--dc-email", args.dc_email), ("--dc-to", args.dc_to)):
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr.strip()):
                sys.exit(f"Error: {label} does not look like a valid email/DeltaChat address")

    if args.video not in (0, 1):
        sys.exit("Error: --video must be 0 or 1")
    if not (1 <= args.sensitivity <= 500):
        sys.exit("Error: --sensitivity must be between 1 and 500")
    if not math.isfinite(args.adapt_minutes) or args.adapt_minutes < 0:
        sys.exit("Error: --adapt-minutes must be a non-negative number")
    if args.cooldown < 0:
        sys.exit("Error: --cooldown cannot be negative")
    if args.max_events < 0:
        sys.exit("Error: --max-events cannot be negative")
    if not math.isfinite(args.interval) or args.interval < 0:
        sys.exit("Error: --interval must be a non-negative number")
    if not math.isfinite(args.heartbeat_hours) or args.heartbeat_hours < 0:
        sys.exit("Error: --heartbeat-hours must be a non-negative number")
    try:
        args.schedule_parsed = parse_schedule(args.schedule)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")


def parse_args():
    p = ArgumentParser()
    p.add_argument("-C", "--camera", help="0 for back camera, 1 for front camera", type=str, default="0")
    p.add_argument("-v", "-V", "--video", help="1 to convert photos to video, 0 to skip (default: 0)", type=int, default=0)
    p.add_argument("-sms", "--sms", help="Phone number to send SMS to")
    p.add_argument("-c", "--call", help="Phone number to call")
    p.add_argument("-st", "--sensitivity", help="Detection sensitivity %% (default: 100)", type=int, default=100)
    p.add_argument("-ch", "--chatid", help="Telegram chat id (or env TG_CHAT_ID)", type=str)
    p.add_argument("-bt", "--bottoken", help="Telegram bot token (or env TG_BOT_TOKEN)", type=str)
    p.add_argument("--ntfy-topic", help="ntfy topic name for push alerts & remote commands (or env NTFY_TOPIC)", type=str)
    p.add_argument("--ntfy-server", help="ntfy server URL (default: https://ntfy.sh or env NTFY_SERVER)", type=str)
    p.add_argument("--ntfy-token", help="ntfy access token if topic is protected (or env NTFY_TOKEN)", type=str)
    p.add_argument("--dc-email", help="DeltaChat / email sender address (or env DC_EMAIL)", type=str)
    p.add_argument("--dc-password", help="DeltaChat / email password (or env DC_PASSWORD)", type=str)
    p.add_argument("--dc-to", help="Recipient DeltaChat address to alert & accept commands from (or env DC_TO)", type=str)
    p.add_argument("--dc-smtp-host", help="SMTP host override (or env DC_SMTP_HOST)", type=str)
    p.add_argument("--dc-smtp-port", help="SMTP port override (default: auto 465/587 or env DC_SMTP_PORT)", type=int)
    p.add_argument("--dc-imap-host", help="IMAP host override (or env DC_IMAP_HOST)", type=str)
    p.add_argument("--dc-imap-port", help="IMAP SSL port (default: 993 or env DC_IMAP_PORT)", type=int)
    p.add_argument("--schedule", help="Active hours in 24h HH:MM-HH:MM format (e.g. 20:00-08:00)", type=str)
    p.add_argument("--cooldown", help="Min seconds between SMS/call alerts (default: 300)", type=int, default=300)
    p.add_argument("--max-events", help="Keep only newest N event folders (0 = keep all, default: 20)",
                   type=int, default=20)
    p.add_argument("--interval", help="Extra seconds to sleep between frames (saves battery)",
                   type=float, default=0.0)
    p.add_argument("--adapt-minutes", type=float, default=15.0,
                   help="Re-tune threshold from calm frames every N minutes (default: 15, 0 = off)")
    p.add_argument("--status-alerts", choices=["off", "important", "all"], default="important",
                   help="Remote status messages: off, important (default), or all")
    p.add_argument("--heartbeat-hours", type=float, default=0.0,
                   help="Send an 'I am alive' status message every N hours (default: 0 = off)")
    p.add_argument("--log", help="Log file path (default: seccam.log)", default="seccam.log")
    p.add_argument("--no-telegram", action="store_true",
                   help="Run without Telegram and skip the credentials prompt")
    args = p.parse_args()

    if args.chatid is None:
        args.chatid = os.environ.get("TG_CHAT_ID")
    if args.bottoken is None:
        args.bottoken = os.environ.get("TG_BOT_TOKEN")
    if args.no_telegram:
        args.chatid = args.bottoken = None

    if args.ntfy_topic is None:
        args.ntfy_topic = os.environ.get("NTFY_TOPIC")
    if args.ntfy_server is None:
        args.ntfy_server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    if args.ntfy_token is None:
        args.ntfy_token = os.environ.get("NTFY_TOKEN")

    if args.dc_email is None:
        args.dc_email = os.environ.get("DC_EMAIL")
    if args.dc_password is None:
        args.dc_password = os.environ.get("DC_PASSWORD")
    if args.dc_to is None:
        args.dc_to = os.environ.get("DC_TO")
    if args.dc_smtp_host is None:
        args.dc_smtp_host = os.environ.get("DC_SMTP_HOST")
    if args.dc_smtp_port is None and os.environ.get("DC_SMTP_PORT"):
        try:
            args.dc_smtp_port = int(os.environ["DC_SMTP_PORT"])
        except ValueError:
            sys.exit("Error: DC_SMTP_PORT must be an integer")
    if args.dc_imap_host is None:
        args.dc_imap_host = os.environ.get("DC_IMAP_HOST")
    if args.dc_imap_port is None:
        try:
            args.dc_imap_port = int(os.environ.get("DC_IMAP_PORT", "993"))
        except ValueError:
            sys.exit("Error: DC_IMAP_PORT must be an integer")

    validate_args(args)
    return args


def check_runtime_environment(args):
    needed = list(REQUIRED_TERMUX_BINARIES)
    if args.sms:
        needed.append("termux-sms-send")
    if args.call:
        needed.append("termux-telephony-call")
    missing = [binary for binary in needed if shutil.which(binary) is None]
    if missing:
        raise RuntimeError(
            "Missing required Termux tools: " + ", ".join(missing) +
            ". Run: pkg install termux-api -y  (and make sure the separate Termux:API "
            "Android app is installed too)."
        )
    if args.video == 1 and shutil.which("ffmpeg") is None:
        raise RuntimeError("Video mode enabled but ffmpeg is not installed. Run: pkg install ffmpeg -y")
