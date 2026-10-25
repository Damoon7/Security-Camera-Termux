#!/usr/bin/env python3
"""Security Camera for Termux (hardened & optimized version).

Key Features:
  - Two-way Telegram control: /pause [min], /resume, /snap, /status, /calibrate,
    /sensitivity <N>, /schedule <HH:MM-HH:MM|off>, /stop, /help.
  - Active hours schedule (--schedule HH:MM-HH:MM, e.g. 20:00-08:00) with automatic
    recalibration when entering the active window.
  - Video default is 0 (skips ffmpeg to save battery/CPU); sends keyframes as one Telegram album.
  - Optimized MP4 encoding (-v 1): caps resolution at 1280p with H.264 veryfast/CRF 25 and
    +faststart for low file size, minimal CPU heat, and instant Telegram streaming.
  - On-the-fly photo optimization: creates lightweight Full-HD JPEG copies for Telegram dispatch,
    then cleans them up (original high-res evidence is kept intact on disk).
  - If a Telegram photo upload fails, a plain text alert is sent instead.
  - If Telegram credentials are missing, asks at startup (or use --no-telegram to skip the question).
  - 2-frame motion confirmation: filters out single-frame autofocus/AE/OIS camera glitches on older
    phones while still preserving both the initial trigger frame (_001.jpeg) and confirmation frame (_002.jpeg).
  - Soft-threshold pixel differencing: prevents thermal sensor noise drift from causing cliff-edge spikes.
  - No shell=True: every command is an argv list (injection-proof).
  - Stale-photo protection: temp photo is deleted before every capture to prevent false negatives.
  - Dynamic threshold: re-tuned every --adapt-minutes from calm frames (clamped to 1.0x-2.5x of
    initial calibration so it never undercuts your sensitivity setting).
  - Auto-recalibration safeguard against persistent noise / ambient light shifts.
  - Telegram status messages (--status-alerts) and optional heartbeat (--heartbeat-hours).
  - Timeouts on all Termux commands to prevent camera lockups.
  - Single background worker queue for orderly uploads.
  - Cooldowns on SMS/call alerts & automatic disk pruning for old events.
"""
import os
import re
import sys
import json
import queue
import shutil
import signal
import logging
import threading
from argparse import ArgumentParser
from datetime import datetime
from getpass import getpass
from glob import glob
from logging.handlers import RotatingFileHandler
from statistics import stdev, median
from subprocess import run, PIPE, TimeoutExpired
from time import sleep, time

import numpy as np
from PIL import Image, ImageFilter, ImageOps

log = logging.getLogger("seccam")

EVENTS_DIR = "events"
TMP_PHOTO = "photo_tmp.jpeg"
TRIGGER_PHOTO = "photo_trigger.jpeg"
SNAP_PHOTO = "photo_snap.jpeg"
PHOTO_TIMEOUT = 40          # seconds for a single termux-camera-photo call
MAX_PHOTOS_PER_EVENT = 300
MAX_TG_SAMPLES_NO_VIDEO = 6
MAX_TG_SAMPLES_WITH_VIDEO = 4
TG_ALBUM_TIMEOUT = 240      # albums of full-size photos need a generous timeout
TG_VIDEO_LIMIT = 49 * 1024 * 1024
REQUIRED_TERMUX_BINARIES = (
    "termux-camera-photo",
    "termux-microphone-record",
    "termux-wake-lock",
    "termux-wake-unlock",
)

HELP_TEXT = (
    "🤖 Available Commands:\n"
    "/status - Show camera status & battery\n"
    "/snap - Take & send an instant photo\n"
    "/pause [min] - Pause monitoring (e.g. /pause 20 or /pause)\n"
    "/resume - Resume monitoring immediately\n"
    "/calibrate - Recalibrate motion sensor now\n"
    "/sensitivity <1-500> - Set sensitivity % (e.g. /sensitivity 80)\n"
    "/schedule <HH:MM-HH:MM|off> - Set active hours (e.g. /schedule 20:00-08:00)\n"
    "/stop - Safely stop the security camera\n"
    "/help - Show this help message"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
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
    """Run an argv list (never through a shell). Returns CompletedProcess or None."""
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


def parse_schedule(spec):
    """Parse 'HH:MM-HH:MM' into (start_minutes, end_minutes) from midnight, or None."""
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


def check_runtime_environment(args):
    """Verify the required Termux utilities exist before the surveillance loop starts."""
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
    if args.video not in (0, 1):
        sys.exit("Error: --video must be 0 or 1")
    if args.adapt_minutes < 0:
        sys.exit("Error: --adapt-minutes cannot be negative")
    if args.cooldown < 0:
        sys.exit("Error: --cooldown cannot be negative")
    if args.max_events < 0:
        sys.exit("Error: --max-events cannot be negative")
    if args.interval < 0:
        sys.exit("Error: --interval cannot be negative")
    if args.heartbeat_hours < 0:
        sys.exit("Error: --heartbeat-hours cannot be negative")
    try:
        args.schedule_parsed = parse_schedule(args.schedule)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")


# --------------------------------------------------------------------------- #
# Image processing
# --------------------------------------------------------------------------- #
def get_normalized_diff(image1, image2):
    if image2.size != image1.size:
        image2 = image2.resize(image1.size)
    arr1 = np.asarray(image1, dtype=np.float32)
    arr2 = np.asarray(image2, dtype=np.float32)
    arr1 = arr1 - np.mean(arr1)
    arr2 = arr2 - np.mean(arr2)

    std1 = float(np.std(arr1))
    std2 = float(np.std(arr2))
    if std1 > 1.0 and std2 > 1.0:
        arr2 = arr2 * (std1 / std2)

    return np.abs(arr1 - arr2)


def dif_of_images(image1, image2, noise_cutoff=10.0):
    diff = get_normalized_diff(image1, image2)
    excess = np.maximum(0.0, diff - noise_cutoff)
    return int(np.sum(excess))


def process_image_file(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with Image.open(filepath) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("L")
            img.thumbnail((320, 320))  # absorbs 1-2px OIS/AF micro-shifts on older phones
            return img.filter(ImageFilter.GaussianBlur(radius=3))
    except Exception:
        return None


def take_processed_photo(camera_id, filename=TMP_PHOTO, keep_file=False):
    remove_if_exists(filename)
    run_cmd(["termux-camera-photo", "-c", camera_id, filename], timeout=PHOTO_TIMEOUT)
    processed = process_image_file(filename)
    if not keep_file or processed is None:
        remove_if_exists(filename)
    return processed


def check_camera(camera_id, attempts=2):
    """Quick test shot so permission/app problems fail fast instead of after 200 slow retries."""
    for _ in range(attempts):
        if take_processed_photo(camera_id) is not None:
            return True
        sleep(2)
    return False


def prepare_tg_photo(filepath, max_dim=1920, quality=82):
    """Creates a temporary downscaled JPEG for fast, low-bandwidth Telegram dispatch."""
    opt_path = filepath + ".tg.jpg"
    try:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(filepath) as img:
            if img.format == "JPEG":
                img.draft("RGB", (max_dim, max_dim))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_dim, max_dim), resample)
            img.convert("RGB").save(opt_path, "JPEG", quality=quality, optimize=True)
        return opt_path
    except Exception as exc:
        log.warning("Could not optimise %s for Telegram (%s); sending original", filepath, exc)
        remove_if_exists(opt_path)
        return filepath


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
def compute_threshold(img_array, sensitivity=100):
    """Learn (threshold, noise_cutoff) from a window of still frames (>= 3)."""
    pairs = [(x, y) for x in range(len(img_array)) for y in range(x + 1, len(img_array))]
    flat = np.concatenate([get_normalized_diff(img_array[x], img_array[y]).ravel()[::2]
                           for x, y in pairs])
    noise_998 = float(np.percentile(flat, 99.8))
    noise_995 = float(np.percentile(flat, 99.5))
    del flat
    noise_cutoff = max(7.0, noise_998 * 1.4)

    probe_delta = [dif_of_images(img_array[x], img_array[y], max(5.0, noise_995)) for x, y in pairs]
    delta = [dif_of_images(img_array[x], img_array[y], noise_cutoff) for x, y in pairs]

    maxdel = max(delta)
    std = stdev(delta) if len(delta) > 1 else 0.0
    probe_max = max(probe_delta)
    probe_std = stdev(probe_delta) if len(probe_delta) > 1 else 0.0

    statistical_bound = int(maxdel * 3.0 + 6.0 * std + probe_max * 1.5 + 3.0 * probe_std)
    area_bound = int(200 * noise_cutoff)
    base_threshold = max(statistical_bound, area_bound)

    sensitivity = max(1, sensitivity)
    threshold = max(1, int(base_threshold * (100.0 / sensitivity)))
    return threshold, noise_cutoff


def setup_num(camera_id, sensitivity=100, min_stable_frames=12, max_photos=200):
    img_array = []
    p995_history = []
    stable_count = 1
    total_taken = 0

    print("Warming up camera...                ", end="\r")
    take_processed_photo(camera_id, keep_file=False)

    while stable_count < min_stable_frames:
        if total_taken >= max_photos:
            if len(img_array) >= 3:
                log.warning("\nScene never fully stabilised; calibrating with current frames.")
                break
            raise RuntimeError("Camera is not producing usable photos.")

        total_taken += 1
        print(f"Calibrating: {stable_count}/{min_stable_frames} stable (photo #{total_taken})   ", end="\r")
        img = take_processed_photo(camera_id, keep_file=False)
        if img is None:
            sleep(1)
            continue

        if img_array:
            diff_mat = get_normalized_diff(img_array[-1], img)
            p995_history.append(float(np.percentile(diff_mat, 99.5)))
            if len(p995_history) > min_stable_frames - 1:
                p995_history.pop(0)

            med_p995 = median(p995_history)
            stable_transitions = 0
            for val in reversed(p995_history):
                if val <= med_p995 * 2.0 + 6.0 and val < 25.0:
                    stable_transitions += 1
                else:
                    break
            stable_count = stable_transitions + 1

        img_array.append(img)
        if len(img_array) > min_stable_frames:
            img_array.pop(0)

        if stable_count < min_stable_frames:
            sleep(1)

    threshold, noise_cutoff = compute_threshold(img_array, sensitivity)
    log.info("\nSet up in %d photos! (Cutoff: %.1f | Thr: %s)", total_taken, noise_cutoff, f"{threshold:,}")
    return threshold, noise_cutoff, img_array[-1]


class ThresholdAdapter:
    """Slowly re-learns threshold and noise cutoff while running.

    Clamped to 1.0x..2.5x of the calibrated baseline so thermal sensor warm-up can raise the
    threshold when needed, but calm periods can never undercut the initial calibration or undo
    a lowered --sensitivity setting.
    """
    WINDOW = 12
    CALM_FRACTION = 0.85
    SMOOTHING = 0.5
    MIN_FACTOR, MAX_FACTOR = 1.0, 2.5

    def __init__(self, sensitivity, minutes, delta, cutoff):
        self.sensitivity = sensitivity
        self.interval = max(0.0, minutes) * 60.0
        self.enabled = self.interval > 0
        self.reset_base(delta, cutoff)

    def reset_base(self, delta, cutoff):
        self.base_delta = float(delta)
        self.base_cutoff = float(cutoff)
        self.frames = []
        self.last_adapt = time()

    def clear(self):
        self.frames = []

    def observe(self, img, dif, delta):
        if not self.enabled:
            return
        if dif <= delta * self.CALM_FRACTION:
            self.frames.append(img)
            if len(self.frames) > self.WINDOW:
                self.frames.pop(0)
        else:
            self.frames = []

    def maybe_adapt(self, delta, cutoff):
        if (not self.enabled or len(self.frames) < self.WINDOW
                or time() - self.last_adapt < self.interval):
            return delta, cutoff
        try:
            new_thr, new_cut = compute_threshold(self.frames, self.sensitivity)
        except Exception:
            log.exception("Threshold adaptation failed; keeping current values")
            self.frames = []
            self.last_adapt = time()
            return delta, cutoff

        def clamp(value, base):
            return min(max(value, base * self.MIN_FACTOR), base * self.MAX_FACTOR)

        a = self.SMOOTHING
        adapted_thr = max(1, int(a * clamp(new_thr, self.base_delta) + (1 - a) * delta))
        adapted_cut = a * clamp(new_cut, self.base_cutoff) + (1 - a) * cutoff
        if abs(adapted_thr - int(delta)) >= max(10, int(delta * 0.02)):
            log.info("\nThreshold adapted: %s -> %s (cutoff %.1f -> %.1f)",
                     f"{int(delta):,}", f"{adapted_thr:,}", cutoff, adapted_cut)
        else:
            adapted_thr = int(delta)
        self.frames = []
        self.last_adapt = time()
        return adapted_thr, adapted_cut


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def tg_request_raw(token, method, fields=None, files=None, retries=3, timeout=90):
    """Call Telegram Bot API and return parsed JSON dict or None."""
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


# --------------------------------------------------------------------------- #
# Background worker
# --------------------------------------------------------------------------- #
def build_video(folder, audio_path, fr):
    video_path = os.path.join(folder, "event.mp4")
    has_audio = os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
    scale_filter = (
        "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fr),
           "-pattern_type", "glob", "-i", os.path.join(folder, "*.jpeg")]
    if has_audio:
        cmd += ["-i", audio_path]
    cmd += [
        "-vf", scale_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "10",
        "-movflags", "+faststart",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    cmd.append(video_path)
    res = run_cmd(cmd, timeout=900)
    if res is None or res.returncode != 0 or not os.path.exists(video_path):
        log.error("ffmpeg failed: %s", (res.stderr[-300:] if res else "timeout"))
        return None
    return video_path


def prune_events(max_events):
    if max_events <= 0 or not os.path.isdir(EVENTS_DIR):
        return
    dirs = sorted(d for d in os.listdir(EVENTS_DIR) if os.path.isdir(os.path.join(EVENTS_DIR, d)))
    for old in dirs[:-max_events]:
        shutil.rmtree(os.path.join(EVENTS_DIR, old), ignore_errors=True)
        log.info("Pruned old event folder: %s", old)


def worker_loop(q, args):
    tg_cfg = None
    if args.chatid and args.bottoken:
        tg_cfg = {"chatid": args.chatid, "bottoken": args.bottoken}
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            break
        kind = item[0]
        try:
            if kind == "snap":
                _, snap_path, caption = item
                if tg_cfg:
                    send_single_photo_to_telegram(tg_cfg, snap_path, caption)
                else:
                    remove_if_exists(snap_path)
            else:
                _, folder, audio_path, fr, photo_count = item
                video_path = build_video(folder, audio_path, fr) if args.video == 1 else None
                if tg_cfg:
                    send_event_to_telegram(tg_cfg, folder, video_path, photo_count)
                prune_events(args.max_events)
        except Exception:
            log.exception("Background processing failed")
        finally:
            q.task_done()


# --------------------------------------------------------------------------- #
# Event recording
# --------------------------------------------------------------------------- #
def record_event(camera_id, first_img, noise_cutoff, delta, trigger_photo=None, second_photo=None):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    folder = os.path.join(EVENTS_DIR, stamp)
    os.makedirs(folder, exist_ok=True)
    audio_path = os.path.join(folder, f"{stamp}.aac")

    run_cmd(["termux-microphone-record", "-f", audio_path, "-e", "aac"], timeout=15)

    start = time()
    photo_index = 0
    failures = 0
    consecutive_calm = 0
    prev_img = first_img

    for saved_tmp in (trigger_photo, second_photo):
        if saved_tmp and os.path.exists(saved_tmp):
            frame_path = os.path.join(folder, f"{stamp}_{photo_index + 1:03d}.jpeg")
            try:
                shutil.move(saved_tmp, frame_path)
                photo_index += 1
            except Exception as exc:
                log.warning("Could not save initial photo: %s", exc)
                remove_if_exists(saved_tmp)

    while photo_index < MAX_PHOTOS_PER_EVENT:
        photo_index += 1
        now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        photo_path = os.path.join(folder, f"{now}_{photo_index:03d}.jpeg")
        print(f"Recording event... captured: {photo_index} photos   ", end="\r")

        run_cmd(["termux-camera-photo", "-c", camera_id, photo_path], timeout=PHOTO_TIMEOUT)
        cur_img = process_image_file(photo_path)
        if cur_img is None:
            photo_index -= 1
            failures += 1
            if failures >= 5:
                log.warning("\nCamera failing during event, ending recording early.")
                break
            continue

        failures = 0
        current_diff = dif_of_images(prev_img, cur_img, noise_cutoff)
        prev_img = cur_img
        consecutive_calm = consecutive_calm + 1 if current_diff <= delta else 0
        if consecutive_calm >= 3:
            break

    run_cmd(["termux-microphone-record", "-q"], timeout=15)

    elapsed = max(1.0, time() - start)
    fr = min(30.0, max(0.05, round(photo_index / elapsed, 3)))
    log.info("\nMotion stopped. Total captured: %d photos in %.0fs.", photo_index, elapsed)
    return folder, audio_path, fr, photo_index, prev_img


# --------------------------------------------------------------------------- #
# Status messages & Remote Control (Telegram)
# --------------------------------------------------------------------------- #
class StatusNotifier:
    """Sends short status messages on their own thread, so the camera loop never waits for the
    network and status messages never queue behind big photo uploads."""

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

    def send(self, text, level="important", force=False):
        if self._wanted(level, force):
            self._q.put(self._clean(text))

    def reply(self, text):
        """Always send direct replies to user commands regardless of --status-alerts mode."""
        self.send(text, force=True)

    def send_limited(self, key, text, min_interval=1800, level="important"):
        now = time()
        if now - self._last_sent.get(key, -1e12) < min_interval:
            return
        if self._wanted(level, False):
            self._last_sent[key] = now
            self._q.put(self._clean(text))

    def _run(self):
        while True:
            text = self._q.get()
            if text is None:
                return
            try:
                tg_request(self.token, "sendMessage", {"chat_id": self.chat, "text": text},
                           retries=2, timeout=30)
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
    """Polls Telegram for incoming bot commands from the authorized chat_id and wakes the main loop."""

    def __init__(self, args, notifier):
        self.token = args.bottoken
        self.chat = str(args.chatid) if args.chatid is not None else None
        self.notifier = notifier
        self.commands = queue.Queue()
        self.wake_event = threading.Event()
        self._stop = False
        self._thread = None
        if self.token and self.chat:
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop = True
        self.wake_event.set()

    def wait(self, seconds):
        self.wake_event.wait(timeout=max(0.0, seconds))
        self.wake_event.clear()

    def _flush_old_updates(self):
        data = tg_request_raw(self.token, "getUpdates", {"offset": "-1", "timeout": "0"},
                              retries=1, timeout=15)
        if data and data.get("result"):
            return data["result"][-1]["update_id"] + 1
        return None

    def _poll_loop(self):
        offset = self._flush_old_updates()
        while not self._stop:
            fields = {"timeout": "20", "allowed_updates": json.dumps(["message"])}
            if offset is not None:
                fields["offset"] = str(offset)
            data = tg_request_raw(self.token, "getUpdates", fields=fields, retries=1, timeout=30)
            if not data or not isinstance(data.get("result"), list):
                sleep(3)
                continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                sender_chat = str((msg.get("chat") or {}).get("id", ""))
                if sender_chat != self.chat:
                    continue
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                parts = text.split(maxsplit=1)
                cmd = parts[0].split("@")[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                self.commands.put((cmd, arg))
                self.wake_event.set()


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
    """Watches battery level and charger state (checked every few minutes)."""
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


def describe_state(paused_until, schedule_parsed):
    now = time()
    if paused_until is not None:
        if paused_until == float("inf"):
            return "⏸️ Paused (until /resume)"
        rem = max(1, int(round((paused_until - now) / 60.0)))
        return f"⏸️ Paused ({rem}m remaining)"
    if not is_within_schedule(schedule_parsed):
        return f"🌙 Standby (outside schedule {format_schedule(schedule_parsed)})"
    return "🟢 Active (monitoring)"


def build_status_report(start_time, events, threshold, sensitivity, paused_until, schedule_parsed, power):
    uptime = int(time() - start_time)
    hours, rest = divmod(uptime, 3600)
    thr_str = f"{int(threshold):,}" if threshold is not None else "uncalibrated"
    return (
        f"📊 Status: {describe_state(paused_until, schedule_parsed)}\n"
        f"Uptime: {hours}h {rest // 60}m | Events: {events}\n"
        f"Threshold: {thr_str} (sensitivity {sensitivity}%)\n"
        f"Schedule: {format_schedule(schedule_parsed)}\n"
        + power.describe()
    )


def telegram_selftest(args):
    """Verify Telegram at startup so wrong credentials are caught now, not at the first event."""
    require_curl()
    if args.status_alerts == "off":
        ok = tg_request(args.bottoken, "getMe", retries=2, timeout=30)
        if ok:
            log.info("Telegram: enabled (token verified; chat id not tested because status alerts are off)")
    else:
        ok = tg_request(args.bottoken, "sendMessage",
                        {"chat_id": args.chatid,
                         "text": "🔧 Security camera is starting. Send /help anytime for remote commands."},
                        retries=2, timeout=30)
        if ok:
            log.info("Telegram: enabled (test message sent)")
    if not ok:
        log.warning("Telegram: connection test FAILED. Check the token and chat id "
                    "(and that you pressed Start in your bot). Alerts will still be attempted.")
    return ok


def resolve_telegram(args):
    """Decide whether Telegram is on. If credentials are missing, ask instead of silently skipping."""
    if args.chatid and args.bottoken:
        telegram_selftest(args)
        return

    if getattr(args, "no_telegram", False):
        log.info("Telegram: disabled (--no-telegram)")
        return

    if not sys.stdin.isatty():
        log.warning("Telegram: disabled (no credentials found and no interactive terminal to ask)")
        return

    try:
        answer = input("Telegram credentials not found. Continue WITHOUT Telegram alerts? [y/N]: ")
    except EOFError:
        answer = "y"
    if answer.strip().lower() in ("y", "yes"):
        log.info("Telegram: disabled (confirmed)")
        return

    try:
        for attempt in range(3):
            token = getpass("Bot token (hidden, leave empty to cancel): ").strip()
            if not token:
                log.info("Telegram: disabled (cancelled)")
                return
            chat = input("Chat ID: ").strip()
            if re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) and re.fullmatch(r"-?\d+", chat):
                args.bottoken, args.chatid = token, chat
                telegram_selftest(args)
                return
            print("Invalid format (token looks like 123456:ABC-..., chat id is a number). Try again.")
    except EOFError:
        pass
    log.warning("Telegram: disabled (no valid credentials entered)")


def status_line(dif, delta):
    cl = datetime.now().strftime("%H:%M:%S")
    print(f"{cl} | Diff: {dif:,} / Thr: {int(delta):,}    ", end="\r")


def parse_args():
    p = ArgumentParser()
    p.add_argument("-C", "--camera", help="0 for back camera, 1 for front camera", type=str, default="0")
    p.add_argument("-v", "-V", "--video", help="1 to convert photos to video, 0 to skip (default: 0)", type=int, default=0)
    p.add_argument("-sms", "--sms", help="Phone number to send SMS to")
    p.add_argument("-c", "--call", help="Phone number to call")
    p.add_argument("-st", "--sensitivity", help="Detection sensitivity %% (default: 100)", type=int, default=100)
    p.add_argument("-ch", "--chatid", help="Telegram chat id (or env TG_CHAT_ID)", type=str)
    p.add_argument("-bt", "--bottoken", help="Telegram bot token (or env TG_BOT_TOKEN)", type=str)
    p.add_argument("--schedule", help="Active hours in 24h HH:MM-HH:MM format (e.g. 20:00-08:00)", type=str)
    p.add_argument("--cooldown", help="Min seconds between SMS/call alerts (default: 300)", type=int, default=300)
    p.add_argument("--max-events", help="Keep only newest N event folders (0 = keep all, default: 20)",
                   type=int, default=20)
    p.add_argument("--interval", help="Extra seconds to sleep between frames (saves battery)",
                   type=float, default=0.0)
    p.add_argument("--adapt-minutes", type=float, default=15.0,
                   help="Re-tune threshold from calm frames every N minutes (default: 15, 0 = off)")
    p.add_argument("--status-alerts", choices=["off", "important", "all"], default="important",
                   help="Telegram status messages: off, important (default), or all "
                        "(adds motion-started pings and every threshold update)")
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
    validate_args(args)
    return args


STOP_REASON = {"text": "stopped by user (Ctrl+C)"}


def _raise_interrupt(signum, frame):
    try:
        name = signal.Signals(signum).name
    except Exception:
        name = str(signum)
    STOP_REASON["text"] = f"received {name} (system shutdown or terminal/session closed)"
    raise KeyboardInterrupt


def main():
    args = parse_args()
    setup_logging(args.log)
    try:
        check_runtime_environment(args)
    except RuntimeError as exc:
        log.error(str(exc))
        raise SystemExit(1)

    if not check_camera(args.camera):
        log.error("Camera test failed. Make sure the Termux:API Android app is installed "
                  "(not only the package) and that it has Camera permission.")
        raise SystemExit(1)

    try:
        resolve_telegram(args)
    except RuntimeError as exc:
        log.error(str(exc))
        raise SystemExit(1)

    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, _raise_interrupt)
    os.makedirs(EVENTS_DIR, exist_ok=True)

    notifier = StatusNotifier(args)
    power = PowerMonitor(notifier)
    remote = TelegramController(args, notifier)
    if args.heartbeat_hours > 0 and not notifier.available:
        log.warning("--heartbeat-hours ignored: Telegram is not enabled")

    jobs = queue.Queue()
    worker = threading.Thread(target=worker_loop, args=(jobs, args), daemon=True)
    worker.start()

    run_cmd(["termux-wake-lock"], timeout=10)

    started = time()
    events_total = 0
    stop_text = None
    last_alert = 0.0
    rapid_triggers = 0
    camera_alarm = False
    paused_until = None
    schedule_parsed = args.schedule_parsed
    heartbeat_interval = args.heartbeat_hours * 3600
    next_heartbeat = started + heartbeat_interval if heartbeat_interval > 0 else None

    delta = None
    noise_cutoff = None
    prev = None
    adapter = None
    was_active = False
    standby_logged = False

    try:
        power.poll(force=True)
        if is_within_schedule(schedule_parsed):
            log.info("Setting up the device (place phone in position)...\n")
            try:
                delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
            except RuntimeError as exc:
                log.error("Calibration failed: %s", exc)
                stop_text = f"❌ Startup failed: {exc}"
                raise SystemExit(1)
            prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
            sleep(1)
            adapter = ThresholdAdapter(args.sensitivity, args.adapt_minutes, delta, noise_cutoff)
            was_active = True
            notifier.send(f"✅ Monitoring started (camera {args.camera})\n"
                          f"Threshold: {int(delta):,}\n"
                          f"Schedule: {format_schedule(schedule_parsed)}\n" + power.describe())
        else:
            log.info("Outside active schedule (%s). Waiting in standby...", format_schedule(schedule_parsed))
            notifier.send(f"🌙 Standby: waiting for schedule window ({format_schedule(schedule_parsed)}).\n"
                          + power.describe())
            standby_logged = True

        fails = 0
        while True:
            try:
                power.poll()
                if next_heartbeat is not None and time() >= next_heartbeat:
                    next_heartbeat = time() + heartbeat_interval
                    notifier.send(
                        build_status_report(started, events_total, delta, args.sensitivity,
                                            paused_until, schedule_parsed, power),
                        force=True
                    )

                # Process any incoming Telegram remote commands
                recalibrate_requested = False
                while not remote.commands.empty():
                    cmd, arg = remote.commands.get_nowait()
                    log.info("\n[Telegram Command] %s %s", cmd, arg)

                    if cmd in ("/help", "/start"):
                        notifier.reply(HELP_TEXT)

                    elif cmd == "/status":
                        power.poll(force=True)
                        notifier.reply(build_status_report(
                            started, events_total, delta, args.sensitivity,
                            paused_until, schedule_parsed, power
                        ))

                    elif cmd == "/pause":
                        if not arg:
                            paused_until = float("inf")
                            notifier.reply("⏸️ Monitoring paused indefinitely. Send /resume to continue.")
                        else:
                            try:
                                mins = float(arg)
                                if mins <= 0:
                                    raise ValueError
                                paused_until = time() + mins * 60.0
                                notifier.reply(f"⏸️ Monitoring paused for {mins:g} minutes.")
                            except ValueError:
                                notifier.reply("⚠️ Usage: /pause [minutes] (e.g. /pause 20 or /pause)")

                    elif cmd == "/resume":
                        paused_until = None
                        if is_within_schedule(schedule_parsed):
                            notifier.reply("▶️ Resuming monitoring...")
                        else:
                            notifier.reply(
                                f"▶️ Pause cleared, but currently outside schedule ({format_schedule(schedule_parsed)}). "
                                "Send /schedule off to monitor now."
                            )

                    elif cmd in ("/snap", "/photo"):
                        notifier.reply("📸 Taking snapshot...")
                        remove_if_exists(SNAP_PHOTO)
                        run_cmd(["termux-camera-photo", "-c", args.camera, SNAP_PHOTO], timeout=PHOTO_TIMEOUT)
                        if os.path.exists(SNAP_PHOTO):
                            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            jobs.put(("snap", SNAP_PHOTO, f"📸 Snapshot ({stamp})"))
                        else:
                            notifier.reply("❌ Snapshot failed (camera did not return a photo).")

                    elif cmd in ("/calibrate", "/recalibrate"):
                        recalibrate_requested = True

                    elif cmd == "/sensitivity":
                        try:
                            new_sens = int(arg)
                            if not (1 <= new_sens <= 500):
                                raise ValueError
                            old_sens = args.sensitivity
                            args.sensitivity = new_sens
                            if delta is not None:
                                delta = max(1, int(delta * (old_sens / float(new_sens))))
                                if adapter is not None:
                                    adapter.sensitivity = new_sens
                                    adapter.reset_base(delta, noise_cutoff)
                            notifier.reply(
                                f"🎯 Sensitivity set to {new_sens}% "
                                + (f"(new threshold: {int(delta):,})" if delta else "")
                            )
                        except ValueError:
                            notifier.reply("⚠️ Usage: /sensitivity <1-500> (e.g. /sensitivity 80)")

                    elif cmd == "/schedule":
                        if not arg:
                            notifier.reply(f"🕒 Current schedule: {format_schedule(schedule_parsed)}\n"
                                           "Usage: /schedule HH:MM-HH:MM or /schedule off")
                        else:
                            try:
                                schedule_parsed = parse_schedule(arg)
                                stand_msg = format_schedule(schedule_parsed)
                                notifier.reply(f"🕒 Schedule updated: {stand_msg}")
                                standby_logged = False
                            except ValueError as exc:
                                notifier.reply(f"⚠️ {exc}")

                    elif cmd == "/stop":
                        STOP_REASON["text"] = "stopped remotely via Telegram (/stop)"
                        raise KeyboardInterrupt

                    else:
                        notifier.reply("❓ Unknown command. Send /help for available commands.")

                # Check if a timed pause has expired
                if paused_until is not None and paused_until != float("inf") and time() >= paused_until:
                    paused_until = None
                    log.info("\nPause timer expired; resuming.")
                    notifier.send("▶️ Pause timer finished. Resuming monitoring.")

                # Handle manual /calibrate request
                if recalibrate_requested:
                    notifier.reply("🔧 Recalibrating sensor, please keep the scene still...")
                    remove_if_exists(TRIGGER_PHOTO)
                    remove_if_exists(TMP_PHOTO)
                    delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                    prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
                    adapter = ThresholdAdapter(args.sensitivity, args.adapt_minutes, delta, noise_cutoff)
                    rapid_triggers = 0
                    was_active = True
                    notifier.reply(f"✅ Calibration complete (Threshold: {int(delta):,}).")
                    continue

                # Check whether we should be actively monitoring right now
                in_sched = is_within_schedule(schedule_parsed)
                is_active = (paused_until is None) and in_sched

                if not is_active:
                    if was_active:
                        was_active = False
                        remove_if_exists(TRIGGER_PHOTO)
                        remove_if_exists(TMP_PHOTO)
                        prev = None
                        if not in_sched and not standby_logged:
                            log.info("\nEntering scheduled standby (%s)...", format_schedule(schedule_parsed))
                            notifier.send(f"🌙 Entering standby (schedule {format_schedule(schedule_parsed)}).")
                            standby_logged = True
                    cl = datetime.now().strftime("%H:%M:%S")
                    state_txt = "PAUSED" if paused_until is not None else f"STANDBY ({format_schedule(schedule_parsed)})"
                    print(f"{cl} | {state_txt}                          ", end="\r")
                    remote.wait(15)
                    continue

                # Transitioning from Standby/Paused -> Active
                if not was_active:
                    standby_logged = False
                    log.info("\nActivating surveillance; calibrating sensor...")
                    notifier.send("🌅 Active window started. Calibrating sensor...")
                    delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                    prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
                    adapter = ThresholdAdapter(args.sensitivity, args.adapt_minutes, delta, noise_cutoff)
                    rapid_triggers = 0
                    was_active = True
                    notifier.send(f"✅ Monitoring active (Threshold: {int(delta):,}).")
                    continue

                # Active monitoring: capture & compare
                img = take_processed_photo(args.camera, keep_file=True)
                if img is None:
                    fails += 1
                    if fails % 10 == 0:
                        log.warning("Camera failed %d times in a row", fails)
                        notifier.send_limited("camera_fail", f"📷 Camera failed {fails} times in a row", 1800)
                        camera_alarm = True
                    remote.wait(min(30, fails))
                    continue
                if camera_alarm:
                    notifier.send("✅ Camera is working again")
                    camera_alarm = False
                fails = 0

                if prev is None:
                    prev = img
                    continue

                dif = dif_of_images(prev, img, noise_cutoff)
                status_line(dif, delta)

                if dif > delta:
                    # Save candidate trigger frame and take 1 immediate confirmation shot
                    remove_if_exists(TRIGGER_PHOTO)
                    if os.path.exists(TMP_PHOTO):
                        try:
                            shutil.move(TMP_PHOTO, TRIGGER_PHOTO)
                        except OSError:
                            remove_if_exists(TMP_PHOTO)

                    verify_img = take_processed_photo(args.camera, keep_file=True)
                    if verify_img is None:
                        remove_if_exists(TRIGGER_PHOTO)
                        prev = img
                        continue

                    verify_dif = dif_of_images(prev, verify_img, noise_cutoff)
                    if verify_dif <= delta:
                        log.info("\nIgnored single-frame camera glitch (Diff: %s, Verify: %s)",
                                 f"{dif:,}", f"{verify_dif:,}")
                        remove_if_exists(TRIGGER_PHOTO)
                        remove_if_exists(TMP_PHOTO)
                        prev = verify_img
                        rapid_triggers = 0
                        continue

                    prev = verify_img
                    rapid_triggers += 1
                    if rapid_triggers >= 3:
                        log.warning("\n⚠️ Persistent high difference detected (noise or lighting shift).")
                        log.info("Auto-recalibrating sensor baseline...")
                        notifier.send("⚠️ Persistent noise or lighting shift detected. Recalibrating: "
                                      "monitoring is paused until this finishes.")
                        remove_if_exists(TRIGGER_PHOTO)
                        remove_if_exists(TMP_PHOTO)
                        rapid_triggers = 0
                        try:
                            delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                        except RuntimeError as exc:
                            log.error("Recalibration failed (%s); keeping previous settings, "
                                      "retrying in 60s", exc)
                            notifier.send("❌ Recalibration failed. Keeping previous settings, "
                                          "retrying in 60s.")
                            prev = None
                            remote.wait(60)
                            continue
                        prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
                        adapter.reset_base(delta, noise_cutoff)
                        notifier.send(f"✅ Recalibration done. Monitoring resumed (threshold {int(delta):,}).")
                        continue

                    log.warning("\n🚨 An External Object Detected (Recording until motion stops...)")
                    events_total += 1
                    notifier.send("🚨 Motion detected, recording...", level="all")

                    if time() - last_alert >= args.cooldown:
                        last_alert = time()
                        stamp = datetime.now().strftime("%H:%M:%S")
                        if args.sms:
                            res = run_cmd(["termux-sms-send", "-n", args.sms,
                                           f"An External Object Detected at {stamp}"], timeout=30)
                            if command_ok(res):
                                log.info("SMS sent to %s", args.sms)
                            else:
                                log.warning("SMS to %s FAILED: %s", args.sms, describe_failure(res))
                        if args.call:
                            res = run_cmd(["termux-telephony-call", args.call], timeout=30)
                            if command_ok(res):
                                log.info("Calling %s", args.call)
                            else:
                                log.warning("Call to %s FAILED: %s", args.call, describe_failure(res))
                    else:
                        log.info("SMS/call suppressed (cooldown active)")

                    folder, audio_path, fr, count, last = record_event(
                        args.camera, verify_img, noise_cutoff, delta,
                        trigger_photo=TRIGGER_PHOTO, second_photo=TMP_PHOTO
                    )
                    jobs.put(("event", folder, audio_path, fr, count))
                    log.info("[Info] Processing & uploading in background. Resuming surveillance...")
                    if last is not None:
                        prev = last
                    adapter.clear()
                else:
                    prev = img
                    rapid_triggers = 0
                    adapter.observe(img, dif, delta)
                    new_delta, new_cutoff = adapter.maybe_adapt(delta, noise_cutoff)
                    if new_delta != delta:
                        notifier.send(f"🎚️ Threshold adapted: {int(delta):,} -> {int(new_delta):,}",
                                      level="all")
                    delta, noise_cutoff = new_delta, new_cutoff

                if args.interval > 0:
                    remote.wait(args.interval)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.exception("Unexpected error in surveillance loop; continuing in 5s")
                notifier.send_limited("loop_error", f"⚠️ Unexpected error: {type(exc).__name__}: {exc}", 1800)
                run_cmd(["termux-microphone-record", "-q"], timeout=15)
                remove_if_exists(TRIGGER_PHOTO)
                remove_if_exists(TMP_PHOTO)
                prev = None
                remote.wait(5)

    except KeyboardInterrupt:
        stop_text = f"🛑 Security camera stopped: {STOP_REASON['text']}"
        log.info("\n\n[Info] Security camera stopped (%s).", STOP_REASON["text"])
    finally:
        remote.stop()
        run_cmd(["termux-wake-unlock"], timeout=10)
        run_cmd(["termux-microphone-record", "-q"], timeout=15)
        remove_if_exists(TRIGGER_PHOTO)
        remove_if_exists(TMP_PHOTO)
        remove_if_exists(SNAP_PHOTO)
        if stop_text:
            notifier.flush(timeout=15)
            notifier.send_now(stop_text, force=True)
        if not jobs.empty() or worker.is_alive():
            log.info("Finishing pending uploads (Ctrl+C again to skip)...")
            try:
                jobs.put(None)
                worker.join(timeout=120)
            except KeyboardInterrupt:
                pass
        print("Device resources released. Goodbye!")


if __name__ == "__main__":
    main()
