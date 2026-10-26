#!/usr/bin/env python3
"""Security Camera for Termux (Modularized Version with Telegram, ntfy & DeltaChat)."""
import os
import re
import sys
import json
import math
import shutil
import queue
import signal
import threading
import py_compile
from datetime import datetime
from getpass import getpass
from glob import glob
from time import sleep, time

from config import (
    parse_args, setup_logging, check_runtime_environment,
    EVENTS_DIR, TMP_PHOTO, TRIGGER_PHOTO, SNAP_PHOTO, PHOTO_TIMEOUT,
    MAX_PHOTOS_PER_EVENT, run_cmd, remove_if_exists, command_ok, describe_failure,
    require_curl, parse_schedule, format_schedule, is_within_schedule, log,
    has_telegram, has_ntfy, has_deltachat, has_any_remote
)
from vision import (
    check_camera, setup_num, ThresholdAdapter, dif_of_images,
    take_processed_photo, process_image_file
)
from power import PowerMonitor
from tg_delta_ntfy_mgr import (
    StatusNotifier, TelegramController, send_event_all,
    send_single_photo_all, tg_request, ntfy_send_text, dc_send_message,
    HELP_TEXT, MAIN_REPLY_KEYBOARD, PAUSE_INLINE_KB,
    SENSITIVITY_INLINE_KB, SCHEDULE_INLINE_KB, STOP_CONFIRM_INLINE_KB
)


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
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            break
        kind = item[0]
        try:
            if kind == "snap":
                _, snap_path, caption = item
                if has_any_remote(args):
                    send_single_photo_all(args, snap_path, caption)
                else:
                    remove_if_exists(snap_path)
            else:
                _, folder, audio_path, fr, photo_count = item
                video_path = build_video(folder, audio_path, fr) if args.video == 1 else None
                if has_any_remote(args):
                    send_event_all(args, folder, video_path, photo_count)
                prune_events(args.max_events)
        except Exception:
            log.exception("Background processing failed")
        finally:
            q.task_done()


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


def describe_state(paused_until, schedule_parsed):
    now = time()
    if paused_until is not None:
        if not math.isfinite(paused_until):
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


def perform_self_update(notifier, remote, jobs, worker):
    """Pulls the latest code from GitHub, verifies syntax, and restarts in-place."""
    if shutil.which("git") is None:
        notifier.reply("❌ Update failed: git is not installed (run: pkg install git -y).")
        return

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    notifier.reply("⏳ Checking for updates from GitHub...")
    res = run_cmd(
        ["git", "-C", repo_dir, "-c", "core.askPass=", "pull", "--ff-only"],
        timeout=60
    )
    if not command_ok(res):
        notifier.reply(f"❌ Update failed (git pull):\n{describe_failure(res)}")
        return

    out = (res.stdout or "").strip()
    if "already up to date" in out.lower() or "already up-to-date" in out.lower():
        notifier.reply("✅ Camera is already running the latest version!")
        return

    for py_file in glob(os.path.join(repo_dir, "*.py")):
        try:
            py_compile.compile(py_file, doraise=True)
        except Exception as exc:
            log.error("Syntax check failed after git pull (%s): %s", py_file, exc)
            notifier.reply(f"⚠️ Update downloaded, but syntax check failed ({os.path.basename(py_file)}). Keeping current process running.")
            return

    summary = out[-300:] if out else "New commits pulled."
    log.info("Update downloaded:\n%s\nRestarting process in-place...", summary)
    notifier.reply(f"📦 Update downloaded:\n{summary}\n\n🔄 Restarting camera now...")

    remote.stop()
    run_cmd(["termux-microphone-record", "-q"], timeout=10)
    remove_if_exists(TRIGGER_PHOTO)
    remove_if_exists(TMP_PHOTO)
    remove_if_exists(SNAP_PHOTO)
    for snap_tmp in glob("snap_*.jpeg*"):
        remove_if_exists(snap_tmp)

    if not jobs.empty() or worker.is_alive():
        try:
            jobs.put(None)
            worker.join(timeout=60)
        except Exception:
            pass

    notifier.flush(timeout=10)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def telegram_selftest(args):
    require_curl()
    if args.status_alerts == "off":
        ok = tg_request(args.bottoken, "getMe", retries=2, timeout=30)
        if ok:
            log.info("Telegram: enabled (token verified; chat id not tested because status alerts are off)")
    else:
        ok = tg_request(
            args.bottoken, "sendMessage",
            {
                "chat_id": args.chatid,
                "text": "🔧 Security camera is starting. Control panel buttons are ready below.",
                "reply_markup": json.dumps(MAIN_REPLY_KEYBOARD),
            },
            retries=2, timeout=30
        )
        if ok:
            log.info("Telegram: enabled (test message & control keyboard sent)")
    if not ok:
        log.warning("Telegram: connection test FAILED. Check the token and chat id.")
    return ok


def ntfy_selftest(args):
    if args.status_alerts == "off":
        log.info("ntfy: enabled (topic %s on %s)", args.ntfy_topic, args.ntfy_server)
        return True
    ok = ntfy_send_text(
        args,
        "🔧 Security camera is starting. Send /status, /snap, /pause 30, /resume, /update, or /stop anytime.",
        retries=2, timeout=25
    )
    if ok:
        log.info("ntfy: enabled (test notification sent to %s/%s)", args.ntfy_server, args.ntfy_topic)
    else:
        log.warning("ntfy: connection test FAILED. Check --ntfy-server / --ntfy-topic / credentials.")
    return ok


def deltachat_selftest(args):
    if args.status_alerts == "off":
        log.info("DeltaChat: enabled (%s -> %s)", args.dc_email, args.dc_to)
        return True
    ok = dc_send_message(
        args,
        "🔧 Security camera is starting. Reply with /status, /snap, /pause 30, /resume, /update, or /stop anytime.",
        retries=2, timeout=45
    )
    if ok:
        log.info("DeltaChat: enabled (test message sent to %s)", args.dc_to)
    else:
        log.warning("DeltaChat: SMTP test FAILED. Check --dc-email / --dc-password / --dc-smtp-host.")
    return ok


def resolve_telegram(args):
    if has_telegram(args):
        telegram_selftest(args)
    if has_ntfy(args):
        ntfy_selftest(args)
    if has_deltachat(args):
        deltachat_selftest(args)
    if has_any_remote(args):
        return

    if getattr(args, "no_telegram", False):
        log.info("Remote alerts: disabled (--no-telegram)")
        return

    if not sys.stdin.isatty():
        log.warning("Remote alerts: disabled (no credentials found)")
        return

    try:
        answer = input("No remote alert credentials (Telegram/ntfy/DeltaChat) found. Continue WITHOUT remote alerts? [y/N]: ")
    except EOFError:
        answer = "y"
    if answer.strip().lower() in ("y", "yes"):
        log.info("Remote alerts: disabled (confirmed)")
        return

    try:
        for _ in range(3):
            token = getpass("Telegram Bot token (hidden, leave empty to cancel): ").strip()
            if not token:
                log.info("Telegram: disabled (cancelled)")
                return
            chat = input("Telegram Chat ID: ").strip()
            if re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) and re.fullmatch(r"-?\d+", chat):
                args.bottoken, args.chatid = token, chat
                telegram_selftest(args)
                return
            print("Invalid format. Try again.")
    except EOFError:
        pass
    log.warning("Remote alerts: disabled (no valid credentials entered)")


STOP_REASON = {"text": "stopped by user (Ctrl+C)"}


def _raise_interrupt(signum, frame):
    try:
        name = signal.Signals(signum).name
    except Exception:
        name = str(signum)
    STOP_REASON["text"] = f"received {name} (system shutdown or terminal/session closed)"
    raise KeyboardInterrupt


def status_line(dif, delta):
    cl = datetime.now().strftime("%H:%M:%S")
    print(f"{cl} | Diff: {dif:,} / Thr: {int(delta):,}    ", end="\r")


def main():
    args = parse_args()
    setup_logging(args.log)
    try:
        check_runtime_environment(args)
    except RuntimeError as exc:
        log.error(str(exc))
        raise SystemExit(1)

    if not check_camera(args.camera):
        log.error("Camera test failed. Make sure Termux:API app is installed with permissions.")
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
        log.warning("--heartbeat-hours ignored: no remote channel (Telegram/ntfy/DeltaChat) is enabled")

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
                          f"Schedule: {format_schedule(schedule_parsed)}\n" + power.describe(),
                          reply_markup=MAIN_REPLY_KEYBOARD)
        else:
            log.info("Outside active schedule (%s). Waiting in standby...", format_schedule(schedule_parsed))
            notifier.send(f"🌙 Standby: waiting for schedule window ({format_schedule(schedule_parsed)}).\n"
                          + power.describe(),
                          reply_markup=MAIN_REPLY_KEYBOARD)
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

                recalibrate_requested = False
                while not remote.commands.empty():
                    cmd, arg = remote.commands.get_nowait()
                    log.info("\n[Remote Command] %s %s", cmd, arg)

                    if cmd in ("/help", "/start", "/menu"):
                        notifier.reply(HELP_TEXT, reply_markup=MAIN_REPLY_KEYBOARD)

                    elif cmd == "/status":
                        power.poll(force=True)
                        notifier.reply(build_status_report(
                            started, events_total, delta, args.sensitivity,
                            paused_until, schedule_parsed, power
                        ))

                    elif cmd == "/pause_menu":
                        notifier.reply("⏸️ Choose how long to pause monitoring (or send /pause <minutes>):",
                                       reply_markup=PAUSE_INLINE_KB)

                    elif cmd == "/pause":
                        if not arg:
                            paused_until = float("inf")
                            notifier.reply("⏸️ Monitoring paused indefinitely. Send /resume to continue.")
                        else:
                            try:
                                mins = float(arg)
                                if not math.isfinite(mins) or mins <= 0 or mins > 525600:
                                    raise ValueError
                                paused_until = time() + mins * 60.0
                                notifier.reply(f"⏸️ Monitoring paused for {mins:g} minutes.")
                            except ValueError:
                                notifier.reply("⚠️ Choose a duration below or send /pause <minutes>:",
                                               reply_markup=PAUSE_INLINE_KB)

                    elif cmd == "/resume":
                        paused_until = None
                        prev = None
                        if adapter is not None:
                            adapter.clear()
                        if is_within_schedule(schedule_parsed):
                            notifier.reply("▶️ Resuming monitoring...")
                        else:
                            notifier.reply(
                                f"▶️ Pause cleared, but currently outside schedule ({format_schedule(schedule_parsed)}).",
                                reply_markup=SCHEDULE_INLINE_KB
                            )

                    elif cmd in ("/snap", "/photo"):
                        notifier.reply("📸 Taking snapshot...")
                        snap_file = f"snap_{int(time() * 1000)}.jpeg"
                        remove_if_exists(snap_file)
                        run_cmd(["termux-camera-photo", "-c", args.camera, snap_file], timeout=PHOTO_TIMEOUT)
                        if os.path.exists(snap_file):
                            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            threading.Thread(
                                target=send_single_photo_all,
                                args=(args, snap_file, f"📸 Snapshot ({stamp})"),
                                daemon=True
                            ).start()
                        else:
                            notifier.reply("❌ Snapshot failed (camera did not return a photo).")

                    elif cmd in ("/calibrate", "/recalibrate"):
                        recalibrate_requested = True

                    elif cmd == "/sensitivity_menu":
                        notifier.reply(
                            f"🎯 Current sensitivity: {args.sensitivity}%\nSelect a preset or send /sensitivity <1-500>:",
                            reply_markup=SENSITIVITY_INLINE_KB
                        )

                    elif cmd == "/sensitivity":
                        if not arg:
                            notifier.reply(
                                f"🎯 Current sensitivity: {args.sensitivity}%\nSelect a preset or send /sensitivity <1-500>:",
                                reply_markup=SENSITIVITY_INLINE_KB
                            )
                        else:
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
                                notifier.reply("⚠️ Select a preset or send /sensitivity <1-500>:",
                                               reply_markup=SENSITIVITY_INLINE_KB)

                    elif cmd == "/schedule_menu":
                        notifier.reply(
                            f"🕒 Current schedule: {format_schedule(schedule_parsed)}\nChoose a schedule or send /schedule HH:MM-HH:MM:",
                            reply_markup=SCHEDULE_INLINE_KB
                        )

                    elif cmd == "/schedule":
                        if not arg:
                            notifier.reply(
                                f"🕒 Current schedule: {format_schedule(schedule_parsed)}\nChoose a schedule or send /schedule HH:MM-HH:MM:",
                                reply_markup=SCHEDULE_INLINE_KB
                            )
                        else:
                            try:
                                schedule_parsed = parse_schedule(arg)
                                stand_msg = format_schedule(schedule_parsed)
                                notifier.reply(f"🕒 Schedule updated: {stand_msg}")
                                standby_logged = False
                            except ValueError as exc:
                                notifier.reply(f"⚠️ {exc}", reply_markup=SCHEDULE_INLINE_KB)

                    elif cmd == "/update":
                        perform_self_update(notifier, remote, jobs, worker)

                    elif cmd == "/stop_ask":
                        notifier.reply("⚠️ Are you sure you want to stop the security camera? (Send /stop confirm to confirm)",
                                       reply_markup=STOP_CONFIRM_INLINE_KB)

                    elif cmd == "/cancel_stop":
                        notifier.reply("👍 Stop cancelled. Camera is still running.")

                    elif cmd == "/stop":
                        if arg.lower() == "confirm":
                            STOP_REASON["text"] = "stopped remotely (/stop)"
                            raise KeyboardInterrupt
                        notifier.reply("⚠️ Confirm stopping the security camera (tap button or send /stop confirm):",
                                       reply_markup=STOP_CONFIRM_INLINE_KB)

                    else:
                        notifier.reply("❓ Unknown command. Use the buttons below or send /help.")

                if paused_until is not None and math.isfinite(paused_until) and time() >= paused_until:
                    paused_until = None
                    prev = None
                    if adapter is not None:
                        adapter.clear()
                    log.info("\nPause timer expired; resuming.")
                    notifier.send("▶️ Pause timer finished. Resuming monitoring.")

                if recalibrate_requested:
                    notifier.reply("🔧 Recalibrating sensor, please keep the scene still...")
                    remove_if_exists(TRIGGER_PHOTO)
                    remove_if_exists(TMP_PHOTO)
                    try:
                        delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                    except RuntimeError as exc:
                        log.error("Manual calibration failed: %s", exc)
                        notifier.reply(f"❌ Calibration failed: {exc}")
                        continue
                    prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
                    adapter = ThresholdAdapter(args.sensitivity, args.adapt_minutes, delta, noise_cutoff)
                    rapid_triggers = 0
                    was_active = is_within_schedule(schedule_parsed)
                    state_note = " (still paused)" if paused_until is not None else ""
                    notifier.reply(f"✅ Calibration complete (Threshold: {int(delta):,}){state_note}.")
                    continue

                in_sched = is_within_schedule(schedule_parsed)
                is_active = (paused_until is None) and in_sched

                if not is_active:
                    remove_if_exists(TRIGGER_PHOTO)
                    remove_if_exists(TMP_PHOTO)
                    prev = None
                    if adapter is not None:
                        adapter.clear()
                    if not in_sched:
                        was_active = False
                        if not standby_logged:
                            log.info("\nEntering scheduled standby (%s)...", format_schedule(schedule_parsed))
                            notifier.send(f"🌙 Entering standby (schedule {format_schedule(schedule_parsed)}).")
                            standby_logged = True
                    cl = datetime.now().strftime("%H:%M:%S")
                    state_txt = "PAUSED" if paused_until is not None else f"STANDBY ({format_schedule(schedule_parsed)})"
                    print(f"{cl} | {state_txt}                          ", end="\r")
                    remote.wait(15)
                    continue

                if not was_active or delta is None:
                    standby_logged = False
                    log.info("\nActivating surveillance; calibrating sensor...")
                    notifier.send("🌅 Active window started. Calibrating sensor...")
                    try:
                        delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                    except RuntimeError as exc:
                        log.error("Scheduled calibration failed (%s); retrying in 60s", exc)
                        notifier.send("❌ Calibration failed. Retrying in 60s.")
                        remote.wait(60)
                        continue
                    prev = last_img if last_img is not None else take_processed_photo(args.camera, keep_file=False)
                    adapter = ThresholdAdapter(args.sensitivity, args.adapt_minutes, delta, noise_cutoff)
                    rapid_triggers = 0
                    was_active = True
                    notifier.send(f"✅ Monitoring active (Threshold: {int(delta):,}).")
                    continue

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
                        notifier.send("⚠️ Persistent noise or lighting shift detected. Recalibrating...")
                        remove_if_exists(TRIGGER_PHOTO)
                        remove_if_exists(TMP_PHOTO)
                        rapid_triggers = 0
                        try:
                            delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
                        except RuntimeError as exc:
                            log.error("Recalibration failed (%s); retrying in 60s", exc)
                            notifier.send("❌ Recalibration failed. Retrying in 60s.")
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
        for snap_tmp in glob("snap_*.jpeg*"):
            remove_if_exists(snap_tmp)
        if stop_text:
            notifier.flush(timeout=15)
            notifier.send_now(stop_text)
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
