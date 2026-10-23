# 📱 Security Camera by Termux

Turn a spare or old Android phone into a motion-detecting security camera using **Termux** and **Python**.

The script continuously watches the room through the device camera. When movement is detected, it records evidence until motion stops, captures ambient audio, and sends notifications via SMS, phone call, and Telegram.

---

## ✨ Key Features

* **Zero-Config Calibration:** On startup the script waits until the phone is placed down and the scene is still, then learns the camera's sensor noise to set a motion threshold automatically. Brightness and contrast are normalized when comparing frames, so gradual lighting changes are tolerated.
* **Dynamic Event Recording:** Keeps recording while motion persists and stops once the scene calms down. The photo that triggered the alarm is always kept as the first evidence frame.
* **Optimized Telegram Alerts:** Sends a downscaled keyframe album that uploads quickly even on slow mobile networks. If the photo upload fails, a plain text alert is sent instead.
* **Call & SMS Notifications:** Sends an SMS and/or places a phone call, with a configurable cooldown to prevent notification spam.
* **Designed for Long-Running Use:** Sequential background task queue, automatic storage pruning, timeouts on all Termux commands, a rotating log file, and an optional frame interval to reduce CPU and battery usage.

---

## 📋 Prerequisites

1. **Install Termux & Termux:API:**
   Install both apps from **F-Droid** or **GitHub Releases**.
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
pkg install python python-pillow python-numpy termux-api curl -y
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

Download the script:

```bash
curl -O https://raw.githubusercontent.com/Damoon7/Security-Camera-Termux/main/SecCam.py
```

---

## 💻 Configuration & Usage

### 1. Telegram Credentials (optional)

Telegram alerts need a bot token and your numeric chat ID:

* Create a bot with [@BotFather](https://t.me/BotFather) to get the token, then press **Start** in your new bot's chat.
* Message [@userinfobot](https://t.me/userinfobot) to get your numeric Chat ID.

Avoid typing the token directly into the terminal: both the command line and `export` commands are saved in your shell history. Instead, store the credentials in a private file.

Create the file with an editor (`pkg install nano -y` if needed):

```bash
nano ~/.seccam_env
```

Put these two lines inside it, then save:

```
export TG_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
export TG_CHAT_ID="123456789"
```

Restrict access to the file and load it before each run (once per Termux session):

```bash
chmod 600 ~/.seccam_env
source ~/.seccam_env
```

The `-ch` / `-bt` command-line options also work, but they take priority over the environment variables and are visible in your shell history.

### 2. Run Surveillance

Default settings (back camera, 100% sensitivity, video disabled):

```bash
python3 SecCam.py
```

Place the phone in position and step out of view. The script waits for the scene to stabilize, calibrates, and starts monitoring.

### Stopping the Script

Press `Ctrl + C`. This releases the wake lock and microphone, removes temporary files, and finishes pending uploads (up to 2 minutes).

---

## ⚙️ Command-Line Options

| Option | Description | Default |
| :--- | :--- | :--- |
| `-C`, `--camera` | Camera ID: `0` (back), `1` (front) | `0` |
| `-v`, `-V`, `--video` | `1` to encode photos and audio into an MP4 (requires ffmpeg), `0` to skip | `0` |
| `-sms`, `--sms` | Phone number for SMS alerts | `None` |
| `-c`, `--call` | Phone number for alert calls | `None` |
| `-st`, `--sensitivity` | Detection sensitivity in percent (higher = more sensitive) | `100` |
| `-ch`, `--chatid` | Numeric Telegram Chat ID (overrides `TG_CHAT_ID`) | `None` |
| `-bt`, `--bottoken` | Telegram Bot Token (overrides `TG_BOT_TOKEN`) | `None` |
| `--cooldown` | Minimum seconds between SMS/call alerts (does **not** limit Telegram) | `300` |
| `--max-events` | Number of newest event folders to keep (`0` keeps all) | `20` |
| `--interval` | Extra sleep in seconds between frames to reduce CPU and battery use | `0.0` |
| `--log` | Path to the rotating log file | `seccam.log` |

Both a chat ID and a bot token are required to enable Telegram.

---

## 📦 Output & Storage

Every event is saved in its own folder: `events/<YYYY-MM-DD_HHMMSS>/`. It contains the original full-resolution photos, the `.aac` audio, and the `.mp4` video (if enabled).

* **Telegram receives samples only:** up to 6 evenly spaced keyframes (4 when video is enabled), downscaled to about 1920 px, plus the video if it is under Telegram's size limit. The full set of originals stays on the phone.
* **Old events are deleted automatically.** Only the newest `--max-events` folders are kept (20 by default). Copy anything you want to keep, or use `--max-events 0` to disable pruning and manage storage yourself.
* **Telegram alerts are sent for every event**, while SMS and calls respect `--cooldown`.

---

## 📌 Examples

### Reduced Power Usage
Waits 1.5 seconds between frames to reduce CPU and battery usage, and keeps only the 10 newest events. Telegram alerts are sent if the environment variables are loaded:

```bash
python3 SecCam.py --interval 1.5 --max-events 10
```

### Video Recording & Calling
Builds an MP4 for each incident and places an alert call (at most one every 3 minutes). With Telegram credentials loaded (see step 1), the video and keyframes are uploaded too:

```bash
python3 SecCam.py -v 1 -c 09120000000 --cooldown 180
```

---

## ⚠️ Limitations

* Detection compares each frame with the previous one. A perfectly still object (for example, someone who stops moving in view) will not keep triggering, and very slow changes are absorbed as normal.
* If the scene produces constant noise (wind, flickering light, trees), the script recalibrates itself automatically. Monitoring is paused for the few minutes this takes.
* Photo capture speed depends on the phone; expect several seconds per frame, so very fast movements can be missed.
* Detection quality depends on camera placement, lighting, and phone model. Test it before relying on it.

## 🔒 Privacy & Legal

This tool records images and audio. Use it only in spaces you own or have permission to monitor, and follow the recording laws and privacy rules of your country. Keep your Telegram bot token private and never commit it to the repository.
