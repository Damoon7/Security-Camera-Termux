# 📱 Security Camera by Termux

Turn your old or spare Android phone into a smart, motion-detecting security camera using **Termux** and **Python**.

This script continuously monitors your environment by capturing and comparing frames. Once motion or an external object is detected, it automatically triggers an alarm workflow: capturing high-speed photos, recording ambient audio, و sending real-time alerts.

---

## ✨ Features

* **Smart Auto-Calibration:** Automatically analyzes the environment on startup to set an optimal motion-detection threshold.
* **Burst Photo & Audio Capture:** Captures a configurable number of photos while recording `.aac` audio as soon as motion is detected.
* **Video Generation:** Optionally merges all captured photos and recorded audio into a timestamped `.mp4` video using `ffmpeg`.
* **SMS & Phone Call Alerts:** Immediately sends an SMS or places a phone call to your number upon detecting an intruder.
* **Telegram Bot Integration:** Automatically uploads the captured evidence directly to your Telegram account.
* **Background Execution:** Uses `termux-wake-lock` to keep the script running smoothly even when the phone screen is locked.

---

## 📋 Prerequisites

1. **Install Termux & Termux:API:**
   Download and install both apps from **F-Droid** or **GitHub Releases**.
   > ⚠️ **Important:** Do **not** install Termux from the Google Play Store, as the Play Store version is deprecated and incompatible with `Termux:API`.

2. **Grant Android Permissions:**
   Go to your phone's **Settings → Apps → Termux:API → Permissions** and enable access to:
   * Camera
   * Microphone
   * SMS
   * Phone

---

## 🚀 Installation

Open **Termux** and run the following commands to update packages and install all required dependencies:

```bash
pkg update && pkg upgrade -y
pkg install python python-pillow python-numpy termux-api ffmpeg curl -y
```

Download the script directly to your device:

```bash
curl -O [https://raw.githubusercontent.com/Damoon7/Security-Camera-Termux/main/SecCam.py](https://raw.githubusercontent.com/Damoon7/Security-Camera-Termux/main/SecCam.py)
```

---

## 💻 Usage

Run the script with default settings (back camera, 30-second countdown, 50 photos per detection):

```bash
python3 SecCam.py
```

To view the help menu:

```bash
python3 SecCam.py -h
```

### Command-Line Options

| Option | Description | Default |
| :--- | :--- | :--- |
| `-C`, `--camera` | Camera ID: `0` for back camera, `1` for front camera | `0` |
| `-v`, `-V`, `--video` | Set to `1` to merge photos and audio into an MP4 video, `0` to disable | `0` |
| `-sms`, `--sms` | Phone number to receive an SMS alert upon detection | `None` |
| `-c`, `--call` | Phone number to call upon detection | `None` |
| `-n`, `--number` | Number of photos to capture when motion is detected | `50` |
| `-s`, `--seconds` | Initial countdown timer (in seconds) before calibration starts | `30` |
| `-ch`, `--chatid` | Your numerical Telegram Chat ID | `None` |
| `-bt`, `--bottoken` | Your Telegram Bot Token | `None` |

> 💡 **Telegram Setup Tip:** 
> 1. Create a bot via [@BotFather](https://t.me/BotFather) to get your `Bot_Token`, and make sure to press **Start** inside your new bot.
> 2. Send a message to [@userinfobot](https://t.me/userinfobot) to find your numerical `Chat_ID`.
> 3. Both `-ch` and `-bt` must be provided to enable Telegram uploads.

---

## 📌 Full Example

Use the front camera (`-C 1`), wait 180 seconds before arming (`-s 180`), capture 200 photos (`-n 200`), convert them into a video (`-v 1`), alert via SMS and phone call, and upload the photos to Telegram:

```bash
python3 SecCam.py -C 1 -v 1 -sms 09120000000 -c 09120000000 -n 200 -s 180 -ch 123456789 -bt 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
```

All captured photos, recorded audio (`.aac`), and generated video files (`.mp4`) are automatically organized into a timestamped folder (e.g., `2026-10-22_143000/`).
