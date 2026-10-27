# 📱 Security Camera by Termux

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Android / Termux](https://img.shields.io/badge/Platform-Android%20%7C%20Termux-3DDC84?logo=android&logoColor=white)](https://termux.dev/)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Channels: Telegram | ntfy | DeltaChat | SMS](https://img.shields.io/badge/Alerts-Telegram%20%7C%20ntfy%20%7C%20DeltaChat%20%7C%20SMS-blue)](#)

Turn a spare or old Android phone into an intelligent, motion-detecting security camera using **Termux** and **Python**.

The project is organized into a clean, modular structure (`SecCam.py`, `config.py`, `vision.py`, `power.py`, `tg_delta_ntfy_mgr.py`) and continuously watches the room through the device camera. When movement is detected, it records evidence until motion stops, captures ambient audio, and sends notifications and media via **Telegram**, **ntfy**, **DeltaChat**, **SMS**, and **phone call**—while letting you control the camera remotely from Telegram, ntfy, or DeltaChat.

---

## ⚡ Quick Start (60 Seconds)

Already have **Termux** and **Termux:API** installed from F-Droid with Camera & Microphone permissions granted? Run these commands in Termux to get started immediately:

```bash
pkg update -y && pkg install python python-pillow python-numpy termux-api git curl -y
git clone https://github.com/Damoon7/Security-Camera-Termux.git
cd Security-Camera-Termux
python3 SecCam.py --ntfy-topic my_secret_cam_CHANGE_THIS
```

> ⚠️ **Important Security Note:** Replace `my_secret_cam_CHANGE_THIS` with your own unique, random topic name (e.g., `cam_` followed by random letters/numbers). Never use a common or default name, as public `ntfy.sh` topics can be subscribed to by anyone who knows the name!
>
> Open the free **ntfy** app on your phone, subscribe to that exact secret topic, and you will instantly receive motion alerts, photos, and control buttons (`/snap`, `/status`, `/pause`). See below to configure **Telegram** and **DeltaChat**!

---

## ✨ Key Features

* **Modular Architecture:** Cleanly separated into independent modules for configuration (`config.py`), vision processing (`vision.py`), power monitoring (`power.py`), multi-channel remote messaging (`tg_delta_ntfy_mgr.py`), and main execution (`SecCam.py`).
* **Multi-Channel Alerts & Remote Control (Telegram + ntfy + DeltaChat):** Use Telegram, ntfy, or DeltaChat—individually or all at the same time! Built entirely with Python's standard library (`urllib`, `smtplib`, `imaplib`, `email`) and `curl`, requiring zero heavy extra packages in Termux.
* **Zero-Config Calibration:** On startup the script waits until the phone is placed down and the scene is still, then learns the camera's sensor noise to set a motion threshold automatically. Brightness and contrast are normalized when comparing frames, so gradual lighting changes are tolerated. While running, the threshold is also re-tuned slowly from calm frames.
* **Interactive Control Panel:** Automatically registers the official Telegram `/` command menu, displays a persistent bottom button pad (`📸 Snap`, `📊 Status`, `⏸️ Pause`, `▶️ Resume`, `🎯 Sensitivity`, `🕒 Schedule`, `🔧 Calibrate`, `🛑 Stop`), attaches quick-action buttons to `ntfy` notifications, and accepts slash commands (`/status`, `/snap`, `/pause`, etc.) across Telegram, ntfy, and DeltaChat.
* **Active Hours Scheduling:** Optionally restrict surveillance to specific hours (for example `--schedule 20:00-08:00`). Outside the active window the camera waits in low-power standby and automatically calibrates when the active window begins.
* **Dynamic Event Recording & Glitch Filter:** Confirming motion across two consecutive frames filters out single-frame autofocus/auto-exposure glitches on older phones, then keeps recording while motion persists and stops once the scene calms down. Both the initial trigger photo and confirmation frame are kept as the first evidence frames.
* **Optimized Media Alerts:** Sends downscaled Full-HD keyframes (and optional 1280p streaming-ready MP4 video) that upload quickly even on slow mobile networks.
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

## 🚀 Full Installation

Open **Termux** and run:

```bash
pkg update && pkg upgrade -y
pkg install python python-pillow python-numpy termux-api git curl -y
```

If you want MP4 video output (`-v 1`), also install ffmpeg:

```bash
pkg install ffmpeg -y
```

> 💡 **Manual Pillow build (if `python-pillow` fails via `pkg`):**
> ```bash
> pkg install clang make libjpeg-turbo freetype -y
> env INCLUDE="$PREFIX/include" LDFLAGS="-lm" pip install Pillow
> ```

Clone the repository to download all project modules (`SecCam.py`, `config.py`, `vision.py`, `power.py`, `tg_delta_ntfy_mgr.py`):

```bash
git clone https://github.com/Damoon7/Security-Camera-Termux.git
cd Security-Camera-Termux
```

---

## 💻 Configuration & Remote Channels (Telegram / ntfy / DeltaChat)

You can enable **Telegram**, **ntfy**, **DeltaChat**, or any combination of them simultaneously. Store your credentials in a private `~/.seccam_env` file so they are not exposed in your shell history:

```bash
nano ~/.seccam_env
```

Add the variables for whichever channels you want to use:

```sh
# --- Option A: Telegram (optional) ---
export TG_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
export TG_CHAT_ID="123456789"

# --- Option B: ntfy push notifications & remote control (optional) ---
export NTFY_TOPIC="seccam_my_secret_topic_9876"
# export NTFY_CMD_TOPIC="seccam_my_command_topic_9876"
# export NTFY_SERVER="https://ntfy.sh"
# export NTFY_TOKEN="tk_your_optional_token"
# export NTFY_USER="your_optional_username"
# export NTFY_PASSWORD="your_optional_password"

# --- Option C: DeltaChat / Standard Email SMTP+IMAP (optional) ---
export DC_EMAIL="camera_account@example.com"
export DC_PASSWORD="your_camera_account_password"
export DC_TO="your_personal_account@example.com"
```

Save and exit nano (`Ctrl + X`, then `Y`, then `Enter`), restrict permissions, and load the file:

```bash
chmod 600 ~/.seccam_env
. ~/.seccam_env
```

### Run Surveillance

Default settings (back camera, 100% sensitivity, 24/7 active, video disabled):

```bash
python3 SecCam.py
```

Place the phone in position and step out of view. The script waits for the scene to stabilize, calibrates, and starts monitoring.

### Stopping the Script

Press `Ctrl + C` in Termux, or tap **🛑 Stop** in Telegram (or send `/stop confirm` via ntfy / DeltaChat). Either method releases the wake lock and microphone, removes temporary files, and finishes pending uploads (up to 2 minutes).

---

## ⚡ Automatic Startup (Termux:Boot)

To make the security camera run automatically whenever the phone boots up or restarts (ideal for unattended placement):

1. Install **Termux:Boot** from F-Droid or GitHub Releases (ensure it matches your Termux installation source).
2. Open the Termux:Boot app once from your Android app drawer to register its background receiver.
3. In Termux, create the boot directory and startup script:
   ```bash
   mkdir -p ~/.termux/boot
   nano ~/.termux/boot/start_cam.sh
   ```
4. Paste the following script inside, then save (`Ctrl + X`, `Y`, `Enter`):
   ```bash
   #!/data/data/com.termux/files/usr/bin/sh
   termux-wake-lock
   sleep 30
   cd "$HOME/Security-Camera-Termux" || exit 1
   if [ -f "$HOME/.seccam_env" ]; then
       . "$HOME/.seccam_env"
   fi
   python3 SecCam.py > seccam_boot.log 2>&1 &
   ```
5. Make the script executable:
   ```bash
   chmod +x ~/.termux/boot/start_cam.sh
   ```

---

## ⚙️ Command-Line Options

| Option | Description | Default |
| :--- | :--- | :--- |
| `-C`, `--camera` | Camera ID: `0` (back), `1` (front) | `0` |
| `-v`, `-V`, `--video` | `1` to encode photos and audio into an MP4 (requires ffmpeg), `0` to skip | `0` |
| `-sms`, `--sms` | Phone number for SMS alerts | `None` |
| `-c`, `--call` | Phone number for alert calls | `None` |
| `-st`, `--sensitivity` | Detection sensitivity in percent, `1`–`500` (higher = more sensitive) | `100` |
| `-ch`, `--chatid` | Numeric Telegram Chat ID (overrides `TG_CHAT_ID`) | `None` |
| `-bt`, `--bottoken` | Telegram Bot Token (overrides `TG_BOT_TOKEN`) | `None` |
| `--ntfy-topic` | Topic name for outgoing ntfy alerts (overrides `NTFY_TOPIC`) | `None` |
| `--ntfy-cmd-topic` | Topic name for incoming ntfy commands if separate (overrides `NTFY_CMD_TOPIC`) | Same as `--ntfy-topic` |
| `--ntfy-server` | ntfy server URL (overrides `NTFY_SERVER`) | `https://ntfy.sh` |
| `--ntfy-token` | Bearer token for protected ntfy topics (overrides `NTFY_TOKEN`) | `None` |
| `--ntfy-user` | Username for ntfy HTTP Basic auth (overrides `NTFY_USER`) | `None` |
| `--ntfy-password` | Password for ntfy HTTP Basic auth (overrides `NTFY_PASSWORD`) | `None` |
| `--dc-email` | DeltaChat / email sender address for the camera (overrides `DC_EMAIL`) | `None` |
| `--dc-password` | DeltaChat / email password for the camera (overrides `DC_PASSWORD`) | `None` |
| `--dc-to` | Your recipient DeltaChat address (overrides `DC_TO`) | `None` |
| `--dc-smtp-host` | Optional SMTP host override (auto-inferred from domain if omitted) | `Auto` |
| `--dc-smtp-port` | Optional SMTP port override (auto-tries 465 SSL then 587 STARTTLS) | `Auto` |
| `--dc-imap-host` | Optional IMAP host override (auto-inferred from domain if omitted) | `Auto` |
| `--dc-imap-port` | Optional IMAP SSL port override | `993` |
| `--schedule` | Active hours in 24h `HH:MM-HH:MM` format (e.g. `20:00-08:00`) | `None` (24/7) |
| `--cooldown` | Minimum seconds between SMS/call alerts | `300` |
| `--max-events` | Number of newest event folders to keep (`0` keeps all) | `20` |
| `--interval` | Extra sleep in seconds between frames to reduce CPU and battery use | `0.0` |
| `--adapt-minutes` | Re-tune the threshold from calm frames every N minutes (`0` turns it off) | `15` |
| `--status-alerts` | Remote status messages: `off`, `important`, or `all` | `important` |
| `--heartbeat-hours` | Send an "I'm alive" message every N hours (`0` = off) | `0` |
| `--log` | Path to the rotating log file | `seccam.log` |
| `--no-telegram` | Skip the startup prompt if no remote channels are configured | off |

---

## 🎮 Remote Control (Telegram, ntfy & DeltaChat)

All remote commands work across **Telegram**, **ntfy**, and **DeltaChat**:

| Button / Command | Description |
| :--- | :--- |
| **📊 Status** (`/status`) | Show current state (`Active`, `Paused`, or `Standby`), uptime, total events, current threshold, active schedule, battery %, charger status, and temperature |
| **📸 Snap** (`/snap` or `/photo`) | Take an instant photo right now and upload it to all enabled channels |
| **⏸️ Pause** (`/pause [minutes]`) | Typing `/pause 20` pauses for 20 minutes; typing `/pause` with no number pauses indefinitely |
| **▶️ Resume** (`/resume`) | Resume motion detection immediately without needing a full recalibration |
| **🎯 Sensitivity** (`/sensitivity [1-500]`) | Adjust sensitivity preset or type e.g. `/sensitivity 80` |
| **🕒 Schedule** (`/schedule [HH:MM-HH:MM\|off]`) | Set active schedule window or `/schedule off` for 24/7 |
| **🔧 Calibrate** (`/calibrate`) | Re-run full sensor calibration right now |
| **🛑 Stop** (`/stop confirm`) | Safely releases hardware resources and exits |
| `/help` (or `/menu`) | Display the command list (and keyboard in Telegram) |

---

## 📜 Release History

- **SecCam v1.0.0** — Stable Security Camera Release (Modular architecture, Telegram/ntfy/DeltaChat alerts & remote control, active schedule, glitch filter, and battery monitoring)
- **SecCam v0.1.0** — Initial 2024 Prototype

For detailed version notes, see [CHANGELOG.md](CHANGELOG.md) or check the GitHub Releases page.

---

## 🔒 Privacy & Legal

This tool records images and audio. Use it only in spaces you own or have permission to monitor, and follow the recording laws and privacy rules of your country. Keep your credentials (`~/.seccam_env`) private and never commit them to the repository.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
