# 📱 Security Camera by Termux

Turn a spare or old Android phone into a motion-detecting security camera using **Termux** and **Python**.

The script is organized into a clean, modular structure (`SecCam.py`, `config.py`, `vision.py`, `power.py`, `telegram_mgr.py`) and continuously watches the room through the device camera. When movement is detected, it records evidence until motion stops, captures ambient audio, and sends notifications via SMS, phone call, and Telegram—while letting you control the camera remotely through an interactive Telegram button panel.

---

## ✨ Key Features

* **Modular Architecture:** Cleanly separated into independent modules for configuration, vision processing, power monitoring, Telegram management, and main execution.
* **Zero-Config Calibration:** On startup the script waits until the phone is placed down and the scene is still, then learns the camera's sensor noise to set a motion threshold automatically. Brightness and contrast are normalized when comparing frames, so gradual lighting changes are tolerated. While running, the threshold is also re-tuned slowly from calm frames (see *Automatic Calibration* below).
* **Interactive Telegram Control Panel:** Automatically registers the official Telegram `/` command menu, displays a persistent bottom button pad (`📸 Snap`, `📊 Status`, `⏸️ Pause`, `▶️ Resume`, `🎯 Sensitivity`, `🕒 Schedule`, `🔧 Calibrate`, `🛑 Stop`), and provides inline buttons for pause durations, sensitivity presets, schedules, and stop confirmation.
* **Active Hours Scheduling:** Optionally restrict surveillance to specific hours (for example `--schedule 20:00-08:00`). Outside the active window the camera waits in low-power standby and automatically calibrates when the active window begins.
* **Dynamic Event Recording:** Confirming motion across two consecutive frames filters out single-frame autofocus/auto-exposure glitches on older phones, then keeps recording while motion persists and stops once the scene calms down. Both the initial trigger photo and confirmation frame are kept as the first evidence frames.
* **Optimized Telegram Alerts:** Sends a downscaled keyframe album (and optional 1280p streaming-ready MP4 video) that uploads quickly even on slow mobile networks. If the photo upload fails, a plain text alert is sent instead.
* **Remote Status Updates:** Telegram messages tell you when the camera is ready, recalibrating, entering standby, having camera trouble, low on battery, or stopped, plus an optional "still alive" heartbeat.
* **Call & SMS Notifications:** Sends an SMS and/or places a phone call, with a configurable cooldown to prevent notification spam.
* **Designed for Long-Running Use:** Sequential background task queue, automatic storage pruning, timeouts on all Termux commands, a rotating log file, and an optional frame interval to reduce CPU and battery usage.

---

## 📋 Prerequisites

1. **Install Termux & Termux:API:**
   Install both apps from **F-Droid** or **GitHub Releases**.
   > ℹ️ You need **both** the Termux:API Android app *and* the `termux-api` package inside Termux (installed below). They are two separate things, and having only one of them is the most common setup problem.
   > ⚠️ **Important:** Do **not** use the Google Play Store version of Termux. It is deprecated and cannot communicate with `Termux:API`.

2. **Grant Permissions:**
   Go to **Settings → Apps → Termux:API → Permissions** and allow:
   * Camera
   * Microphone
   * SMS
   * Phone

3. **Disable battery optimization for Termux (important for long runs):**
   In your Android settings, set Termux's battery usage to **Unrestricted** (wording and location vary by device). Otherwise Android may kill the script after a while, even with the wake lock.

4. **Keep the phone plugged in.** Continuous camera use drains the battery quickly and heats the device. Place it somewhere with airflow.

---

## 🚀 Installation

Open **Termux** and run:

```bash
pkg update && pkg upgrade -y
pkg install python python-pillow python-numpy termux-api git curl -y
