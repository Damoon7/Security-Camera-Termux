#!/usr/bin/env python3
"""Security Camera for Termux (hardened & optimized version).

Key Features:
  - Video default is 0 (skips ffmpeg to save battery/CPU); sends keyframes as one Telegram album.
  - On-the-fly photo optimization: creates lightweight Full-HD JPEG copies for Telegram dispatch,
    then cleans them up (original high-res evidence is kept intact on disk).
  - If a Telegram photo upload fails, a plain text alert is sent instead.
  - If Telegram credentials are missing, asks at startup (or use --no-telegram to skip the question).
  - Preserves the initial trigger photo as the first evidence frame (_001.jpeg).
  - No shell=True: every command is an argv list (injection-proof).
  - Stale-photo protection: temp photo is deleted before every capture to prevent false negatives.
  - Dynamic threshold: re-tuned every --adapt-minutes from calm frames only (smoothed, clamped
    to 0.5x-2x of the last full calibration, every change logged).
  - Auto-recalibration safeguard against persistent noise / ambient light shifts.
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
PHOTO_TIMEOUT = 40          # seconds for a single termux-camera-photo call
MAX_PHOTOS_PER_EVENT = 300
MAX_TG_SAMPLES_NO_VIDEO = 6
MAX_TG_SAMPLES_WITH_VIDEO = 4
TG_ALBUM_TIMEOUT = 240      # albums of full-size photos need a generous timeout
TG_VIDEO_LIMIT = 49 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def setup_logging(path):
    log.setLevel(logging.INFO)
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
    significant = np.where(diff > noise_cutoff, diff, 0)
    return int(np.sum(significant))


def process_image_file(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with Image.open(filepath) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("L")
            img.thumbnail((580, 580))  # keeps aspect ratio
            return img.filter(ImageFilter.GaussianBlur(radius=2))
    except Exception:
        return None


def take_processed_photo(camera_id, filename=TMP_PHOTO, keep_file=False):
    # Always delete any previous file first: if the camera call fails or times out,
    # we must get None back instead of silently re-reading a stale photo.
    remove_if_exists(filename)
    run_cmd(["termux-camera-photo", "-c", camera_id, filename], timeout=PHOTO_TIMEOUT)
    processed = process_image_file(filename)
    if not keep_file or processed is None:
        remove_if_exists(filename)
    return processed


def prepare_tg_photo(filepath, max_dim=1920, quality=82):
    """Creates a temporary downscaled JPEG for fast, low-bandwidth Telegram dispatch."""
    opt_path = filepath + ".tg.jpg"
    try:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(filepath) as img:
            if img.format == "JPEG":
                # Decode at reduced scale (much faster/lighter on phones); must precede load()
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
    # Subsample pixels per pair so peak memory stays low on phones
    flat = np.concatenate([get_normalized_diff(img_array[x], img_array[y]).ravel()[::2]
                           for x, y in pairs])
    noise_998 = float(np.percentile(flat, 99.8))
    noise_995 = float(np.percentile(flat, 99.5))
    del flat
    noise_cutoff = max(7.0, noise_998 * 1.35)

    probe_delta = [dif_of_images(img_array[x], img_array[y], max(5.0, noise_995)) for x, y in pairs]
    delta = [dif_of_images(img_array[x], img_array[y], noise_cutoff) for x, y in pairs]

    maxdel = max(delta)
    std = stdev(delta) if len(delta) > 1 else 0.0
    probe_max = max(probe_delta)
    probe_std = stdev(probe_delta) if len(probe_delta) > 1 else 0.0

    statistical_bound = int(maxdel * 2.5 + 6.0 * std + probe_max * 1.5 + 3.0 * probe_std)
    area_bound = int(500 * noise_cutoff)
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

    Only a contiguous run of calm frames (difference below half the threshold) is used,
    so frames containing motion are never learned. Changes are smoothed and clamped to
    0.5x..2x of the last full calibration, so sensitivity cannot drift away unnoticed.
    """
    WINDOW = 12
    CALM_FRACTION = 0.5
    SMOOTHING = 0.5
    MIN_FACTOR, MAX_FACTOR = 0.5, 2.0

    def __init__(self, sensitivity, minutes, delta, cutoff):
        self.sensitivity = sensitivity
        self.interval = max(0.0, minutes) * 60.0
        self.enabled = self.interval > 0
        self.reset_base(delta, cutoff)

    def reset_base(self, delta, cutoff):
        """Called after a full (re)calibration: new reference values, empty window."""
        self.base_delta = float(delta)
        self.base_cutoff = float(cutoff)
        self.frames = []
        self.last_adapt = time()

    def clear(self):
        """Called after an event: frames around an event must never be learned."""
        self.frames = []

    def observe(self, img, dif, delta):
        if not self.enabled:
            return
        if dif <= delta * self.CALM_FRACTION:
            self.frames.append(img)
            if len(self.frames) > self.WINDOW:
                self.frames.pop(0)
        else:
            self.frames = []  # window must be a contiguous calm run

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
        log.info("\nThreshold adapted: %s -> %s (cutoff %.1f -> %.1f)",
                 f"{int(delta):,}", f"{adapted_thr:,}", cutoff, adapted_cut)
        self.frames = []
        self.last_adapt = time()
        return adapted_thr, adapted_cut


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def tg_request(token, method, fields=None, files=None, retries=3, timeout=90):
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
                return True
            code = data.get("error_code")
            if code == 429:
                sleep(min(int(data.get("parameters", {}).get("retry_after", 5)), 60))
                continue
            if code in (400, 401, 403, 404):
                log.error("Telegram %s rejected (%s): %s", method, code, data.get("description"))
                return False
        sleep(2 * attempt)
    log.error("Telegram %s failed after %d attempts", method, retries)
    return False


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
            # Photos didn't make it: at least tell the owner something happened
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
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fr),
           "-pattern_type", "glob", "-i", os.path.join(folder, "*.jpeg")]
    if has_audio:
        cmd += ["-i", audio_path]
    cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264",
            "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "10"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
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
        folder, audio_path, fr, photo_count = item
        try:
            video_path = build_video(folder, audio_path, fr) if args.video == 1 else None
            if tg_cfg:
                send_event_to_telegram(tg_cfg, folder, video_path, photo_count)
        except Exception:
            log.exception("Background processing failed for %s", folder)
        finally:
            prune_events(args.max_events)
            q.task_done()


# --------------------------------------------------------------------------- #
# Event recording
# --------------------------------------------------------------------------- #
def record_event(camera_id, first_img, noise_cutoff, delta, trigger_photo=None):
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

    # Preserve the initial trigger photo as the first evidence frame (_001.jpeg)
    if trigger_photo and os.path.exists(trigger_photo):
        first_frame_path = os.path.join(folder, f"{stamp}_{photo_index + 1:03d}.jpeg")
        try:
            shutil.move(trigger_photo, first_frame_path)
            photo_index += 1
        except Exception as exc:
            log.warning("Could not save trigger photo: %s", exc)
            remove_if_exists(trigger_photo)

    while photo_index < MAX_PHOTOS_PER_EVENT:
        photo_index += 1
        now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        photo_path = os.path.join(folder, f"{now}_{photo_index:03d}.jpeg")
        print(f"Recording event... captured: {photo_index} photos   ", end="\r")

        run_cmd(["termux-camera-photo", "-c", camera_id, photo_path], timeout=PHOTO_TIMEOUT)
        cur_img = process_image_file(photo_path)
        if cur_img is None:
            photo_index -= 1  # keep numbering and count honest
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
# Main
# --------------------------------------------------------------------------- #
def telegram_selftest(args):
    """Send a one-off message so wrong credentials are caught at startup, not at the first event."""
    ok = tg_request(args.bottoken, "sendMessage",
                    {"chat_id": args.chatid, "text": "✅ Security camera started"},
                    retries=2, timeout=30)
    if ok:
        log.info("Telegram: enabled (test message sent)")
    else:
        log.warning("Telegram: test message FAILED. Check the token and chat id "
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

    # User wants Telegram: collect credentials right here (token is typed hidden, not saved anywhere)
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
    p.add_argument("--cooldown", help="Min seconds between SMS/call alerts (default: 300)", type=int, default=300)
    p.add_argument("--max-events", help="Keep only newest N event folders (0 = keep all, default: 20)",
                   type=int, default=20)
    p.add_argument("--interval", help="Extra seconds to sleep between frames (saves battery)",
                   type=float, default=0.0)
    p.add_argument("--adapt-minutes", type=float, default=15.0,
                   help="Re-tune threshold from calm frames every N minutes (default: 15, 0 = off)")
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


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    args = parse_args()
    setup_logging(args.log)
    resolve_telegram(args)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    os.makedirs(EVENTS_DIR, exist_ok=True)

    jobs = queue.Queue()
    worker = threading.Thread(target=worker_loop, args=(jobs, args), daemon=True)
    worker.start()

    run_cmd(["termux-wake-lock"], timeout=10)
    log.info("Setting up the device (place phone in position)...\n")

    last_alert = 0.0
    rapid_triggers = 0

    try:
        delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
        prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
        sleep(1)
        adapter = ThresholdAdapter(args.sensitivity, args.adapt_minutes, delta, noise_cutoff)

        fails = 0
        while True:
            # keep_file=True so the photo can be preserved as evidence if it triggers
            img = take_processed_photo(args.camera, keep_file=True)
            if img is None:
                fails += 1
                if fails % 10 == 0:
                    log.warning("Camera failed %d times in a row", fails)
                sleep(min(30, fails))
                continue
            fails = 0

            if prev is None:
                prev = img
                continue

            dif = dif_of_images(prev, img, noise_cutoff)
            prev = img
            status_line(dif, delta)
            adapter.observe(img, dif, delta)

            if dif > delta:
                rapid_triggers += 1
                # 3 triggers back-to-back with no calm frame in between: likely persistent
                # noise or a lighting shift, so re-learn the baseline.
                if rapid_triggers >= 3:
                    log.warning("\n⚠️ Persistent high difference detected (noise or lighting shift).")
                    log.info("Auto-recalibrating sensor baseline...")
                    remove_if_exists(TMP_PHOTO)
                    delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                    prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
                    adapter.reset_base(delta, noise_cutoff)
                    rapid_triggers = 0
                    continue

                log.warning("\n🚨 An External Object Detected (Recording until motion stops...)")

                if time() - last_alert >= args.cooldown:
                    last_alert = time()
                    stamp = datetime.now().strftime("%H:%M:%S")
                    if args.sms:
                        run_cmd(["termux-sms-send", "-n", args.sms,
                                 f"An External Object Detected at {stamp}"], timeout=30)
                        log.info("SMS sent to %s", args.sms)
                    if args.call:
                        run_cmd(["termux-telephony-call", args.call], timeout=30)
                        log.info("Calling %s", args.call)
                else:
                    log.info("SMS/call suppressed (cooldown active)")

                folder, audio_path, fr, count, last = record_event(
                    args.camera, img, noise_cutoff, delta, trigger_photo=TMP_PHOTO
                )
                jobs.put((folder, audio_path, fr, count))
                log.info("[Info] Processing & uploading in background. Resuming surveillance...")
                if last is not None:
                    prev = last
                adapter.clear()
            else:
                rapid_triggers = 0
                delta, noise_cutoff = adapter.maybe_adapt(delta, noise_cutoff)

            if args.interval > 0:
                sleep(args.interval)

    except KeyboardInterrupt:
        log.info("\n\n[Info] Security camera stopped by user.")
    finally:
        run_cmd(["termux-wake-unlock"], timeout=10)
        run_cmd(["termux-microphone-record", "-q"], timeout=15)
        remove_if_exists(TMP_PHOTO)
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
