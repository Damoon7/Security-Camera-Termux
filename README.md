# 📱 Security Camera by Termux

Turn your old or spare Android phone into a smart, motion-detecting security camera using **Termux** and **Python**.

This script continuously monitors your environment by capturing and comparing frames. Once motion is detected, it automatically triggers an alarm workflow: dynamically recording the event until motion stops, recording ambient audio, and seamlessly uploading evidence to Telegram in the background without missing a second of surveillance.

---

## ✨ Features

* **Zero-Config Smart Calibration:** Automatically waits until you place the phone down and leave the frame, then learns the camera's sensor noise and ambient lighting to set an optimal motion-detection threshold.
* **Dynamic Event-Triggered Recording:** The camera smartly records as long as motion persists and automatically stops when the environment becomes calm.
* **Background Multithreading:** Video conversion (`ffmpeg`) and Telegram uploads run in a background thread, allowing the camera to resume surveillance instantly without missing any activity.
* **Video Generation & Audio:** Automatically merges captured frames and `.aac` audio into a timestamped `.mp4` video.
* **SMS & Phone Call Alerts:** Immediately sends an SMS or places a phone call to your number upon detecting an intruder.
* **Telegram Bot Integration:** Automatically uploads photos and video directly to your Telegram account.
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

> 💡 **Alternative Method (if `python-pillow` fails to install via `pkg`):**
> You can compile and install `Pillow` manually using `pip`:
> ```bash
> pkg install clang make libjpeg-turbo freetype -y
> env INCLUDE="$PREFIX/include" LDFLAGS="-lm" pip install Pillow
> ```

Next, download the script directly to your device:

```bash
curl -O https://raw.githubusercontent.com/Damoon7/Security-Camera-Termux/main/SecCam.py
```

---

## 💻 Usage

Run the script with default settings (back camera, 100% sensitivity):

```bash
python3 SecCam.py
```

Once started, place your phone in the desired position and step out of the camera's view. The script will automatically wait until the device and scene stabilize, calibrate itself, and begin monitoring.

### Stopping the Script
To safely stop the camera and release all device resources (wake locks and microphone), press:
```bash
Ctrl + C
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
| `-st`, `--sensitivity` | Detection sensitivity percentage (higher = more sensitive, lower = less sensitive) | `100` |
| `-ch`, `--chatid` | Your numerical Telegram Chat ID | `None` |
| `-bt`, `--bottoken` | Your Telegram Bot Token | `None` |

> 💡 **Telegram Setup Tip:** 
> 1. Create a bot via [@BotFather](https://t.me/BotFather) to get your `Bot_Token`, and make sure to press **Start** inside your new bot.
> 2. Send a message to [@userinfobot](https://t.me/userinfobot) to find your numerical `Chat_ID`.
> 3. Both `-ch` and `-bt` must be provided to enable Telegram uploads.

---

## 📌 Full Example

Use the front camera (`-C 1`), convert event frames into a video (`-v 1`), set sensitivity to `100%` (`-st 100`), alert via SMS and phone call, and upload the files to Telegram:

```bash
python3 SecCam.py -C 1 -v 1 -sms 09120000000 -c 09120000000 -st 100 -ch 123456789 -bt 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
```

All captured photos, recorded audio (`.aac`), and generated video files (`.mp4`) are automatically organized into a timestamped folder (e.g., `2026-10-23_143000/`).
