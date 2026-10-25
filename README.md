📱 Security Camera by Termux
Turn a spare or old Android phone into a motion-detecting security camera using Termux and Python.

The script is organized into a clean, modular structure (SecCam.py, config.py, vision.py, power.py, telegram_mgr.py) and continuously watches the room through the device camera. When movement is detected, it records evidence until motion stops, captures ambient audio, and sends notifications via SMS, phone call, and Telegram—while letting you control the camera remotely through an interactive Telegram button panel.

✨ Key Features
Modular Architecture: Cleanly separated into independent modules for configuration, vision processing, power monitoring, Telegram management, and main execution.
Zero-Config Calibration: On startup the script waits until the phone is placed down and the scene is still, then learns the camera's sensor noise to set a motion threshold automatically. Brightness and contrast are normalized when comparing frames, so gradual lighting changes are tolerated. While running, the threshold is also re-tuned slowly from calm frames (see Automatic Calibration below).
Interactive Telegram Control Panel: Automatically registers the official Telegram / command menu, displays a persistent bottom button pad (📸 Snap, 📊 Status, ⏸️ Pause, ▶️ Resume, 🎯 Sensitivity, 🕒 Schedule, 🔧 Calibrate, 🛑 Stop), and provides inline buttons for pause durations, sensitivity presets, schedules, and stop confirmation.
Active Hours Scheduling: Optionally restrict surveillance to specific hours (for example --schedule 20:00-08:00). Outside the active window the camera waits in low-power standby and automatically calibrates when the active window begins.
Dynamic Event Recording: Confirming motion across two consecutive frames filters out single-frame autofocus/auto-exposure glitches on older phones, then keeps recording while motion persists and stops once the scene calms down. Both the initial trigger photo and confirmation frame are kept as the first evidence frames.
Optimized Telegram Alerts: Sends a downscaled keyframe album (and optional 1280p streaming-ready MP4 video) that uploads quickly even on slow mobile networks. If the photo upload fails, a plain text alert is sent instead.
Remote Status Updates: Telegram messages tell you when the camera is ready, recalibrating, entering standby, having camera trouble, low on battery, or stopped, plus an optional "still alive" heartbeat.
Call & SMS Notifications: Sends an SMS and/or places a phone call, with a configurable cooldown to prevent notification spam.
Designed for Long-Running Use: Sequential background task queue, automatic storage pruning, timeouts on all Termux commands, a rotating log file, and an optional frame interval to reduce CPU and battery usage.
📋 Prerequisites
Install Termux & Termux:API: Install both apps from F-Droid or GitHub Releases.

ℹ️ You need both the Termux:API Android app and the termux-api package inside Termux (installed below). They are two separate things, and having only one of them is the most common setup problem. ⚠️ Important: Do not use the Google Play Store version of Termux. It is deprecated and cannot communicate with Termux:API.

Grant Permissions: Go to Settings → Apps → Termux:API → Permissions and allow:

Camera
Microphone
SMS
Phone
Disable battery optimization for Termux (important for long runs): In your Android settings, set Termux's battery usage to Unrestricted (wording and location vary by device). Otherwise Android may kill the script after a while, even with the wake lock.

Keep the phone plugged in. Continuous camera use drains the battery quickly and heats the device. Place it somewhere with airflow.

🚀 Installation
Open Termux and run:

pkg update && pkg upgrade -y
pkg install python python-pillow python-numpy termux-api git curl -y
If you want MP4 video output (-v 1), also install ffmpeg:

pkg install ffmpeg -y
💡 Manual Pillow build (if python-pillow fails via pkg):

pkg install clang make libjpeg-turbo freetype -y
env INCLUDE="$PREFIX/include" LDFLAGS="-lm" pip install Pillow
Clone the repository to get all modular files (SecCam.py, config.py, vision.py, power.py, telegram_mgr.py):

git clone https://github.com/Damoon7/Security-Camera-Termux.git
cd Security-Camera-Termux
💻 Configuration & Usage
1. Telegram Credentials (optional)
Telegram alerts and remote control need a bot token and your numeric chat ID:

Create a bot with @BotFather to get the token, then press Start in your new bot's chat.
Message @userinfobot to get your numeric Chat ID.
Avoid typing the token directly into the terminal: both the command line and export commands are saved in your shell history. Instead, store the credentials in a private file.

Create the file with an editor (pkg install nano -y if needed):

nano ~/.seccam_env
Type or paste these two lines inside it (use your own values):

export TG_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
export TG_CHAT_ID="123456789"
Then save and exit nano: press Ctrl + X, then Y (to confirm saving), then Enter.

💡 In Termux, tap the CTRL key in the extra-keys row above the keyboard, then press X.

Restrict access to the file and load it before each run (once per Termux session):

chmod 600 ~/.seccam_env
source ~/.seccam_env
💡 Tip: To load these credentials automatically in every new Termux session, append source ~/.seccam_env to ~/.bashrc:

echo 'source ~/.seccam_env' >> ~/.bashrc
The -ch / -bt command-line options also work, but they take priority over the environment variables and are visible in your shell history.

What if the credentials aren't loaded? The script never silently skips Telegram. If it finds no credentials at startup, it asks:

Telegram credentials not found. Continue WITHOUT Telegram alerts? [y/N]:
Type y to run without Telegram.
Press Enter (or n) to type your bot token (hidden as you type) and chat ID right there. They are used for this run only and are not saved; use the private file above to make them permanent.
When credentials are present, the script sends a short "starting" message at startup and attaches the control keyboard, so a wrong token or chat ID shows up immediately instead of at the first alarm. (With --status-alerts off it only verifies the token and sends no message.) Use --no-telegram to skip the question entirely. If the script runs without an interactive terminal (for example from an auto-start script), it can't ask, so it logs that Telegram is disabled and continues.

2. Run Surveillance
Default settings (back camera, 100% sensitivity, 24/7 active, video disabled):

python3 SecCam.py
Place the phone in position and step out of view. The script waits for the scene to stabilize, calibrates, and starts monitoring.

Stopping the Script
Press Ctrl + C in Termux, or tap 🛑 Stop in your Telegram bot and confirm. Either method releases the wake lock and microphone, removes temporary files, and finishes pending uploads (up to 2 minutes).

⚡ Automatic Startup (Termux:Boot)
To make the security camera run automatically whenever the phone boots up or restarts (ideal for unattended placement):

Install Termux:Boot from F-Droid or GitHub Releases (ensure it matches your Termux installation source).
Open the Termux:Boot app once from your Android app drawer to register its background receiver.
In Termux, create the boot directory and startup script:
mkdir -p ~/.termux/boot
nano ~/.termux/boot/start_cam.sh
Paste the following script inside, then save (Ctrl + X, Y, Enter):
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd $HOME/Security-Camera-Termux
if [ -f ~/.seccam_env ]; then
    source ~/.seccam_env
fi
python3 SecCam.py > seccam_boot.log 2>&1 &
Make the script executable:
chmod +x ~/.termux/boot/start_cam.sh
Now, whenever the phone powers on, Termux will automatically acquire a wake-lock, navigate to the project directory, load your Telegram credentials, and start the surveillance script in the background.

⚙️ Command-Line Options
Option	Description	Default
-C, --camera	Camera ID: 0 (back), 1 (front)	0
-v, -V, --video	1 to encode photos and audio into an MP4 (requires ffmpeg), 0 to skip	0
-sms, --sms	Phone number for SMS alerts	None
-c, --call	Phone number for alert calls	None
-st, --sensitivity	Detection sensitivity in percent (higher = more sensitive)	100
-ch, --chatid	Numeric Telegram Chat ID (overrides TG_CHAT_ID)	None
-bt, --bottoken	Telegram Bot Token (overrides TG_BOT_TOKEN)	None
--schedule	Active hours in 24h HH:MM-HH:MM format (e.g. 20:00-08:00)	None (24/7)
--cooldown	Minimum seconds between SMS/call alerts (does not limit Telegram)	300
--max-events	Number of newest event folders to keep (0 keeps all)	20
--interval	Extra sleep in seconds between frames to reduce CPU and battery use	0.0
--adapt-minutes	Re-tune the threshold from calm frames every N minutes (0 turns it off)	15
--status-alerts	Telegram status messages: off, important, or all (see Telegram Status Messages)	important
--heartbeat-hours	Send an "I'm alive" message every N hours (0 = off)	0
--log	Path to the rotating log file	seccam.log
--no-telegram	Run without Telegram and skip the startup question about credentials	off
Both a chat ID and a bot token are required to enable Telegram. If only one is provided, the script exits with an error.

🎮 Remote Control via Telegram
When Telegram is enabled, a background polling thread listens for button taps and commands sent to your bot—no manual setup in @BotFather is required:

Persistent Control Pad & / Menu: At startup, the script automatically registers the official Telegram / command menu and displays a persistent 8-button control pad at the bottom of your chat.
Interactive Inline Sub-Menus: Tapping ⏸️ Pause, 🎯 Sensitivity, 🕒 Schedule, or 🛑 Stop opens inline buttons right under the message so you can pick a preset with one tap (or type a custom value).
Authorized chat only: Commands and button taps are accepted only from your configured TG_CHAT_ID; messages from any other user or chat are ignored.
Stale commands are skipped: On startup, any old unread messages sent while the script was offline are flushed so a previous /stop or /pause never triggers accidentally.
Hardware-safe execution: Camera actions (/snap, /calibrate) are handed off to the main surveillance loop so two camera processes never collide.
Button / Command	Description
📊 Status (/status)	Show current state (Active, Paused, or Standby), uptime, total events, current threshold, active schedule, battery %, charger status, and temperature
📸 Snap (/snap or /photo)	Take an instant photo right now and upload it to Telegram (works even while paused or in scheduled standby)
⏸️ Pause (/pause [minutes])	Opens inline buttons (15m, 30m, 1h, 2h, ♾️ Indefinitely), or type /pause 20 for a custom duration
▶️ Resume (/resume)	Resume motion detection immediately
🎯 Sensitivity (/sensitivity [1-500])	Opens inline presets (50%, 75%, 100%, 125%, 150%), or type e.g. /sensitivity 80
🕒 Schedule (/schedule [HH:MM-HH:MM|off])	Opens inline schedule presets (20:00-08:00, 22:00-07:00, 08:00-17:00, 24/7), or type a custom range
🔧 Calibrate (/calibrate)	Re-run full sensor calibration right now
🛑 Stop (/stop)	Asks for confirmation (✅ Yes, Stop Camera / ❌ Cancel), then safely releases resources and exits
/help (or /menu)	Re-display the bottom control pad and command list
🕒 Active Hours Schedule
If you only want surveillance during specific hours (such as overnight or while you are at work), pass --schedule HH:MM-HH:MM in 24-hour format or set it anytime via the 🕒 Schedule button in Telegram:

Supports both overnight ranges that cross midnight (e.g. 20:00-08:00) and same-day ranges (e.g. 08:30-17:30).
Outside active hours (Standby): The camera stops taking photos to save battery and keep the phone cool, while Telegram remote commands (/status, /snap, /schedule, /stop) remain responsive.
Entering active hours: When the scheduled window starts, the script wakes up, runs a fresh sensor calibration for the current lighting conditions, and begins monitoring automatically.
🎚️ Automatic Calibration
The script keeps its motion threshold up to date in two ways:

Gradual adaptation (--adapt-minutes, default 15): camera sensor noise changes with temperature and light (day vs. night, a lamp turning on, phone warming up on charger). Every N minutes the script re-evaluates the noise level from a continuous run of 12 calm frames with no pause in monitoring, while frames around an event are discarded. Each update is smoothed and clamped between 1.0x and 2.5x of the last full calibration, so sensor warm-up or low-light noise can raise the threshold when needed, but calm periods never undercut your initial calibration or -st sensitivity setting. Meaningful changes (≥2%) are written to the log, for example Threshold adapted: 85,060 -> 91,776. Use --adapt-minutes 0 to turn it off.
Emergency recalibration: if confirmed motion triggers 3 times in a row with no calm frame in between (wind, flickering light, a bumped phone), the script assumes the old baseline no longer fits and repeats the full startup calibration. Monitoring is paused while this runs.
Each adaptation takes a few seconds of processing on the phone, which delays one frame. On a very slow device you can raise the interval, for example --adapt-minutes 30.

📡 Telegram Status Messages
When Telegram is enabled, the camera also reports what it is doing, so silence doesn't leave you guessing. Status messages are sent on their own thread, so they never slow down the camera or wait behind photo uploads. Direct replies to your remote commands and button taps are always sent regardless of --status-alerts.

--status-alerts important (default)

Message	When
🔧 Starting	The script started and is calibrating (step out of view)
✅ Monitoring started / active	Calibration finished; shows the threshold, schedule, and battery
🌙 Standby / 🌅 Active window started	Entering or leaving the --schedule window
▶️ Pause timer finished	A timed /pause <minutes> expired and monitoring resumed
⚠️ Recalibrating / ✅ Recalibration done / ❌ failed	Emergency recalibration (monitoring is paused meanwhile)
📷 Camera failed N times / ✅ Camera is working again	The camera stopped returning photos (reported at most every 30 min)
🔋 Battery low	Battery at 20% and again at 10% while not charging
🔌 Charger disconnected / connected	Power source changed (checked every 5 minutes)
⚠️ Unexpected error	An internal error was caught; the script keeps running (at most every 30 min)
🛑 Stopped	Ctrl+C, /stop, or the system/terminal ended the script
--status-alerts all adds a "🚨 Motion detected, recording..." ping the moment an event starts (the photo album only arrives after the event ends) and every gradual threshold update. --status-alerts off sends no automatic status messages (command replies and motion alerts with photos are still sent).

Heartbeat (--heartbeat-hours N) sends a periodic status report every N hours: current state, uptime, events since start, threshold, schedule, battery, and temperature. It is sent even when --status-alerts off, because it is its own option.

⚠️ A dead phone can't announce it. If the battery dies, the power is cut, or Android force-kills the process, no code can send a message. The "Stopped" message is best-effort (it works when the system asks the script to end politely). The reliable signal is a missing heartbeat: if you expect one every 6 hours (--heartbeat-hours 6) and it doesn't arrive, something is wrong.

📦 Output & Storage
Every event is saved in its own folder: events/<YYYY-MM-DD_HHMMSS>/. It contains the original full-resolution photos, the .aac audio, and the .mp4 video (if enabled).

Telegram receives optimized samples: up to 6 evenly spaced keyframes (4 when video is enabled), downscaled to about 1920 px, plus the MP4 video (capped at 1280 px, H.264 CRF 25 with faststart for small file size and instant streaming). The full set of high-resolution originals stays on the phone.
Old events are deleted automatically. Only the newest --max-events folders are kept (20 by default). Copy anything you want to keep, or use --max-events 0 to disable pruning and manage storage yourself.
Telegram alerts are sent for every event, while SMS and calls respect --cooldown.
📌 Examples
Reduced Power Usage
Waits 1.5 seconds between frames to reduce CPU and battery usage, and keeps only the 10 newest events. Telegram alerts and interactive remote controls are active if the environment variables are loaded:

python3 SecCam.py --interval 1.5 --max-events 10
Night-Only Surveillance (8 PM to 8 AM)
Monitors automatically from 20:00 to 08:00 every night, stays in standby during the day, and sends a heartbeat every 6 hours:

python3 SecCam.py --schedule 20:00-08:00 --heartbeat-hours 6
Video Recording & Calling
Builds an MP4 for each incident and places an alert call (at most one every 3 minutes). With Telegram credentials loaded (see step 1), the video and keyframes are uploaded too:

python3 SecCam.py -v 1 -c 09120000000 --cooldown 180
🛠️ Troubleshooting & Self-Checks
At startup the script checks that the required Termux tools exist and takes one test photo. If something is wrong it stops with a clear message instead of failing silently:

Message	What to do
Missing required Termux tools: ...	Run pkg install termux-api -y (and pkg install ffmpeg -y for -v 1). Also install the separate Termux:API Android app.
Camera test failed ...	Open Android Settings → Apps → Termux:API → Permissions and allow Camera. Make sure no other app is using the camera.
Telegram needs curl ...	Run pkg install curl -y.
Calibration failed ...	The camera returned no usable photos during setup. Check the camera test above and try again.
SMS to ... FAILED / Call to ... FAILED	Grant the SMS / Phone permissions to Termux:API. The reason reported by Android is shown in the message.
While running, an unexpected error is logged to seccam.log and the script carries on after a few seconds instead of exiting. If a mid-run recalibration fails, it keeps the previous settings and retries later.

⚠️ Limitations
Detection compares each frame with the previous one. A perfectly still object (for example, someone who stops moving in view) will not keep triggering, and very slow changes are absorbed as normal.
If the scene produces constant noise (wind, flickering light, trees), the script recalibrates itself automatically. Monitoring is briefly paused while this takes place.
Gradual adaptation learns from calm frames only, and may slowly raise the threshold as the camera sensor warms up or ambient light drops. Check seccam.log for Threshold adapted lines during the first nights.
Status messages and remote commands need a working internet connection. During an outage status messages are retried briefly and then dropped, and remote commands are processed once connectivity returns.
Photo capture speed depends on the phone; expect several seconds per frame, so very fast movements can be missed.
Detection quality depends on camera placement, lighting, and phone model. Test it before relying on it.
🔒 Privacy & Legal
This tool records images and audio. Use it only in spaces you own or have permission to monitor, and follow the recording laws and privacy rules of your country. Keep your Telegram bot token private and never commit it to the repository.