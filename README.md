# NFC Data Logger - Prototype

This project is an NFC-based task tracking system built with a Raspberry Pi Pico W / Pico 2 W, designed to log worker interactions and assembly times for a specific sequence of product boxes. The system guides the user through the process using an OLED display, LED feedback, and sound cues, while simultaneously sending data to a Google Sheet for analysis.

## Video Demo
[Youtube link](https://youtu.be/dNafTuqlBv4)


## Features

- **NFC Tag Scanning**: Recognizes predefined worker badges and part tags (boxes).
- **Interactive Feedback**: Uses an SSD1306 OLED display, a single NeoPixel LED, and a piezo buzzer to guide the worker and provide immediate feedback.
- **Strict Process Flow Enforcement**: Ensures workers check in first, scan boxes in the correct order, and check out properly.
- **Time Tracking**: Calculates the elapsed time for each box and compares it against expected estimates.
- **Offline Mode Fallback**: If the Wi-Fi connection fails, the device continues to function fully in offline mode, showing the timers directly on the OLED.
- **Online Stress-Reduction Mode**: When connected to Wi-Fi, the live timers and "Too slow" warnings are hidden from the user to reduce stress, while still logging the exact data to the cloud.
- **Google Sheets Logging**: Uses HTTP POST requests to log events (`checkin`, `box_start`, `box_done`, `checkout`) directly to a Google Sheet.

## Hardware Setup

The system uses the following hardware components:
* **Microcontroller**: Raspberry Pi Pico 2 W (or Pico W)
* **NFC Reader**: PN532 (Connected via I2C0: SDA on Pin 4, SCL on Pin 5)
* **Display**: SSD1306 OLED 128x64 (Connected via I2C1: SDA on Pin 14, SCL on Pin 15)
* **Status LED**: 1x NeoPixel (Connected to Pin 16)
* **Audio Feedback**: Piezo Buzzer (Connected via PWM on Pin 20)

## Interaction Flow (State Machine)

The application follows a strict state machine based on the provided flowchart:

1. **Idle State**: 
   - Screen: `"Scan worker tag."`
   - Wait for a worker to scan their ID tag. (Scanning a box tag here results in an error).
2. **Checked-In State**:
   - Screen: `"Welcome {Name}."`
   - The worker must now scan the first box in the sequence. 
   - Scanning the wrong box triggers a `"Wrong part tag, expected: {BatchNr}"` error.
   - Scanning a different worker tag triggers a `"Let {Name} check-out."` error.
3. **Task Running State (Batch)**:
   - Screen (Online): `"{BatchNr} In progress..."`
   - Screen (Offline): `"{BatchNr}"`, `"{timer}"`, `"Est {EstTime}"`
   - The system tracks how long the box takes.
   - Scanning the *same* box tag ends the task.
   - Scanning a *different* box tag triggers a `"First check-out {BatchNr}."` error.
4. **Task Completion**:
   - The elapsed time (`{ElapsedTime}`) is compared against the estimated time (`{EstTime}`).
   - The difference (`{TimeDiff}`) determines the result. In offline mode, exceeding the time shows a `"Too slow"` warning; otherwise, a Checkmark Symbol is shown.
   - The task is added to the `{BatchList}` (Task overview).
5. **Product Completion**:
   - Once all 4 boxes are completed in sequence, the system shows `"Product completed."` with a Checkmark, waits 3 seconds, and automatically checks the worker out.

## Box Sequence & Estimates

The system requires boxes to be scanned in the following strict order, with the corresponding estimated completion times:

| Box Order | Estimated Time |
| :--- | :--- |
| **Box 1** | 03:22 (202s) |
| **Box 2** | 02:44 (164s) |
| **Box 3** | 00:30 (30s) |
| **Box 4** | 01:09 (69s) |

## Cloud Logging

When connected to Wi-Fi, the device pushes data to a Google Apps Script Web App attached to a Google Sheet. The following events are logged:
* `checkin`
* `box_start`
* `box_done`
* `checkout`

The exact time taken and the performance status (`Slow` or `OK`) are always sent to the Google Sheet, giving researchers accurate data while keeping the interface stress-free for the worker in online mode.

## How to Run

1. Flash the Pico W with a compatible MicroPython firmware.
2. Upload the project files to the device.
3. Create a `device_secrets.py` file based on your credentials:
   ```python
   WIFI_SSID = "YourNetworkName"
   WIFI_PASSWORD = "YourNetworkPassword"
   SHEET_URL = "https://script.google.com/macros/s/.../exec"
   SHEET_SECRET = "YourSecretKey"
   ```
4. Run `main.py`. The device will attempt to connect to Wi-Fi. If it fails, it will gracefully fall back to offline mode.

## Code Info
This code was written with the help of Gemini 3.1 Pro and Claude Sonnet 5