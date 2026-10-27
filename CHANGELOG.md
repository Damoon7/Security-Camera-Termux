# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [SecCam v1.0.0] - 2026-10-27

### Added
- Stable motion detection and zero-config sensor calibration workflow
- Multi-channel alerts via Telegram, ntfy, DeltaChat, SMS, and phone call notifications
- Two-way remote command support (`/status`, `/snap`, `/pause`, `/resume`, `/schedule`, `/calibrate`, `/sensitivity`, and `/stop`) across Telegram, ntfy, and DeltaChat
- Interactive Telegram control panel (persistent bottom reply keyboard + inline preset buttons) and ntfy action buttons
- Active-hours scheduling (`--schedule HH:MM-HH:MM`) with low-power standby mode
- Continuous background threshold adaptation (`--adapt-minutes`) and hardware battery/charger monitoring
- Improved long-running reliability on Android/Termux with background upload worker, log rotation, and automatic event pruning (`--max-events`)
- Optional streaming-ready MP4 video & AAC audio recording when `ffmpeg` is installed (`-v 1`)

### Changed
- Refactored the monolithic script into clean modular files (`SecCam.py`, `config.py`, `vision.py`, `power.py`, `tg_delta_ntfy_mgr.py`) for easier maintenance
- Improved motion detection stability and reduced false positives via brightness/contrast normalization
- Improved power management and wake-lock handling during unattended monitoring
- Improved Termux:Boot startup script (`dash` shell compatibility with `.` and network wait)

### Fixed
- Two-frame glitch filter to eliminate single-frame autofocus and auto-exposure false alarms
- Prevented camera hardware collisions between motion recording and remote `/snap` commands
- Prevented duplicate command execution on network reconnects via message ID tracking (`seen_ids`)
- Improved cleanup of temporary files and runtime resources on exit

### Security
- Support for storing sensitive credentials in a private `~/.seccam_env` file (`chmod 600`)
- Support for ntfy Bearer tokens, HTTP Basic auth, and separated read/write command topics (`NTFY_CMD_TOPIC`)
- Authorized sender verification for Telegram (`TG_CHAT_ID`) and DeltaChat (`DC_TO`)
- Clear reminder to only monitor spaces you own or are authorized to monitor

---

## [SecCam v0.1.0] - 2024-12-02

### Added
- Initial Android/Termux proof-of-concept for motion detection
- Early frame-differencing experiments and threshold calculation
- Basic camera capture, SMS/call trigger, and Telegram upload workflow
- Initial public repository baseline

### Notes
- This represents the initial 2024 prototype stage of the project
- It later evolved into the modular, multi-channel architecture represented by v1.0.0
