# Badge/box scan event loop for the NFC Data Logger (Pico 2 W)
# Flow: idle -> worker check-in -> box scan (batch running) -> checkout (worker or box) -> idle/checked-in
import sys
import time
import framebuf
import urequests
import wifi
import device_secrets as secrets
from machine import Pin, I2C, PWM
from neopixel import NeoPixel
from pn532 import PN532_I2C
from ssd1306 import SSD1306_I2C

# --- Known tags (see /memories/repo/nfc-tag-ids.md) ---
# Dictionary linking the unique NFC tag IDs (in hexadecimal) to the box names.
BOX_TAGS = {
    bytes.fromhex("046a5052cd6681"): "Box 1",
    bytes.fromhex("04665052cd6681"): "Box 2",
    bytes.fromhex("4cad01d8"): "Box 3",
    bytes.fromhex("92e800d8"): "Box 4",
}

# Dictionary linking the unique NFC tag IDs to the worker names.
WORKER_TAGS = {
    bytes.fromhex("1bf72a54"): "Mike",
    bytes.fromhex("db8b2454"): "Daniel",
}

# The estimated time (in seconds) it should take to complete each box.
BOX_EST_SECONDS = {
    "Box 1": 202,  # 03:22
    "Box 2": 164,  # 02:44
    "Box 3": 30,   # 00:30
    "Box 4": 69,   # 01:09
}

# The required sequence in which the boxes must be scanned.
BOX_ORDER = ["Box 1", "Box 2", "Box 3", "Box 4"]

# State constants for the state machine to know what the logger is currently doing.
STATE_IDLE = 0          # Waiting for a worker to scan their badge.
STATE_CHECKED_IN = 1    # Worker is scanned in, waiting for a box to be scanned.
STATE_RUNNING = 2       # A box is currently being worked on (batch running).

# OLED screen dimensions in pixels.
OLED_W = 128
OLED_H = 64

# --- Hardware setup ---
# Initialize the I2C bus for the NFC reader (SDA on Pin 4, SCL on Pin 5).
i2c_nfc = I2C(0, sda=Pin(4), scl=Pin(5), freq=400000)

# Initialize the I2C bus for the OLED display (SDA on Pin 14, SCL on Pin 15).
i2c_oled = I2C(1, sda=Pin(14), scl=Pin(15), freq=400000)

# Setup the OLED display using its I2C connection.
oled = SSD1306_I2C(OLED_W, OLED_H, i2c_oled)

# Setup the PN532 NFC reader using its I2C connection and standard I2C address (0x24).
nfc = PN532_I2C(i2c_nfc, addr=0x24)
nfc.SAM_configuration() # Configure the NFC module to read smart cards and tags.

# Setup the single NeoPixel LED on Pin 16.
neopixel = NeoPixel(Pin(16), 1)

# Setup the buzzer using Pulse Width Modulation (PWM) on Pin 20.
buzzer = PWM(Pin(20))
buzzer.duty_u16(0) # Start with the buzzer turned off (0% duty cycle).


def log_event(event, box="", elapsed_s=None, status=""):
    # Sends data to a Google Sheet using an HTTP POST request.
    # This is "fire-and-forget", meaning if it fails, it won't crash the main program.
    
    # If the sheet URL is not configured or there is no WiFi connection, do nothing.
    if not secrets.SHEET_URL or not wifi.ip():
        return
        
    try:
        # Make the request with the required event details and the secret key.
        resp = urequests.post(
            secrets.SHEET_URL,
            json={
                "secret": secrets.SHEET_SECRET,
                "worker": current_worker,
                "event": event,
                "box": box,
                "elapsed_s": elapsed_s if elapsed_s is not None else "",
                "status": status,
            },
        )
        resp.close() # Always close the response to free up memory.
    except Exception as e:
        # If something goes wrong (e.g., connection lost), print the error to the console.
        print("log_event failed:", e)


def set_led(r, g, b):
    # Changes the color of the NeoPixel LED.
    # r, g, b should be 1 (on) or 0 (off) to keep it simple, or fractional for brightness.
    # It multiplies by 255 because NeoPixel expects values from 0 to 255.
    neopixel[0] = (r * 255, g * 255, b * 255)
    neopixel.write() # Send the color data to the LED hardware.


def tone(freq, duration_ms):
    # Plays a sound on the buzzer at a specific frequency (pitch) for a specific duration.
    buzzer.freq(freq) # Set the pitch.
    buzzer.duty_u16(32768) # Turn on the buzzer (50% duty cycle = 32768 out of 65535).
    time.sleep_ms(duration_ms) # Wait for the specified duration.
    buzzer.duty_u16(0) # Turn the buzzer back off.


# The following functions play specific melodies for different events.
def beep_checkin():
    # Happy two-tone sound for when a worker checks in successfully.
    tone(1500, 100)
    time.sleep_ms(40)
    tone(2200, 120)


def beep_checkout():
    # Descending two-tone sound for when a worker checks out.
    tone(2200, 100)
    time.sleep_ms(40)
    tone(1200, 150)


def beep_scan_ok():
    # Short high-pitched beep indicating a box was scanned successfully.
    tone(1800, 100)


def beep_batch_done():
    # Ascending three-tone melody when a box task is completely finished.
    tone(1000, 80)
    time.sleep_ms(30)
    tone(1500, 80)
    time.sleep_ms(30)
    tone(2000, 120)


def beep_error():
    # Three low-pitched, long beeps indicating something went wrong.
    for _ in range(3):
        tone(300, 150)
        time.sleep_ms(60)


def show_lines(*lines):
    # Clears the screen and draws multiple lines of small text on the OLED.
    # Mainly used for the "task overview" list showing multiple completed boxes.
    oled.fill(0) # Clear screen with black.
    for i, line in enumerate(lines):
        oled.text(line, 0, i * 8) # Each line of text is 8 pixels high.
    oled.show() # Push the updates to the display.


def draw_scaled_text(text, x, y, scale):
    # Takes normal small text (8x8 pixels) and scales it up to make it larger and more readable.
    w = len(text) * 8
    # Create a tiny hidden screen buffer just for the text.
    tmp = framebuf.FrameBuffer(bytearray(w), w, 8, framebuf.MONO_VLSB)
    tmp.text(text, 0, 0, 1)
    # Loop through every pixel in the hidden buffer and draw a scaled-up square on the main screen.
    for ty in range(8):
        for tx in range(w):
            if tmp.pixel(tx, ty): # If the pixel is part of a letter (not background)
                oled.framebuf.fill_rect(x + tx * scale, y + ty * scale, scale, scale, 1)


def wrap_words(text, max_chars):
    # Splits a long string of text into multiple lines so it fits within the screen's width (max_chars).
    lines = []
    cur = ""
    for word in text.split(" "):
        candidate = word if not cur else cur + " " + word
        if len(candidate) <= max_chars:
            cur = candidate # The word fits on the current line, add it.
        else:
            if cur:
                lines.append(cur) # Current line is full, save it.
            cur = word # Start a new line with the current word.
    if cur:
        lines.append(cur) # Save any remaining text as the last line.
    return lines


def best_fit_lines(text, max_h, max_w=OLED_W):
    # Figures out the largest possible text scale (1x, 2x, 3x, 4x) that will still fit the text on screen.
    # Uses "\n" in the text to force a line break, and then word-wraps each segment.
    segments = text.split("\n")
    best_lines, best_scale = segments, 1
    # Try scales from largest (4) down to normal (1).
    for scale in (4, 3, 2, 1):
        max_chars = max(1, max_w // (8 * scale)) # How many characters fit per line at this scale
        max_lines = max_h // (8 * scale)         # How many lines fit vertically at this scale
        lines = []
        for seg in segments:
            lines.extend(wrap_words(seg, max_chars))
        # If the text fits horizontally and vertically at this scale, we use this scale!
        if lines and len(lines) <= max_lines and all(len(l) <= max_chars for l in lines):
            best_lines, best_scale = lines, scale
            break
    return best_lines, best_scale


def draw_text_block(text, y0, y1):
    # Automatically scales and centers a block of text vertically between coordinates y0 and y1.
    lines, scale = best_fit_lines(text, y1 - y0)
    char_h = 8 * scale
    # Calculate starting Y position to vertically center the block.
    y = y0 + max(0, ((y1 - y0) - len(lines) * char_h) // 2)
    for line in lines:
        # Calculate starting X position to horizontally center each line.
        x = max(0, (OLED_W - len(line) * 8 * scale) // 2)
        draw_scaled_text(line, x, y, scale)
        y += char_h # Move down for the next line.


def draw_checkmark(cx, cy, size):
    # Manually draws a checkmark shape by drawing lines based on a center point (cx, cy) and size.
    # The `for off in (-1, 0, 1)` part makes the lines thicker (3 pixels wide).
    for off in (-1, 0, 1):
        oled.framebuf.line(cx - size + off, cy, cx - size // 3 + off, cy + size, 1)
        oled.framebuf.line(cx - size // 3, cy + size + off, cx + size, cy - size + off, 1)


def show_prompt(text):
    # Clears the screen and displays a large, centered text message.
    oled.fill(0)
    draw_text_block(text, 0, OLED_H) # Use the entire height of the screen.
    oled.show()


def mmss(seconds):
    # Converts a total number of seconds into a string formatted as "MM:SS" (Minutes:Seconds).
    m, s = divmod(seconds, 60)
    return "{:02d}:{:02d}".format(m, s)


# --- Display screens for different states and events ---

def show_idle():
    # Waiting for anyone to scan. LED is white.
    set_led(1, 1, 1)
    show_prompt("Scan worker tag.")


def show_welcome():
    # Worker checked in successfully. LED is green.
    set_led(0, 1, 0)
    show_prompt("Welcome\n" + current_worker)


def show_goodbye():
    # Worker is checking out. LED is green.
    set_led(0, 1, 0)
    show_prompt("Goodbye\n" + current_worker)


def show_scan_worker_first():
    # Someone tried to scan a box before scanning a worker tag. LED is red.
    set_led(1, 0, 0)
    show_prompt("Scan worker tag first.")


def show_wrong_worker():
    # A different worker scanned their tag while someone else was checked in. LED is red.
    set_led(1, 0, 0)
    show_prompt("Let " + current_worker + " check-out.")


def show_wrong_box_order(expected_label):
    # Worker scanned the wrong box (out of order). LED is red.
    set_led(1, 0, 0)
    show_prompt("Wrong part tag,\nexpected: " + expected_label)


def show_must_checkout_current():
    # Worker tried to start a new box before finishing the current one. LED is red.
    set_led(1, 0, 0)
    show_prompt("First check-out " + current_box + ".")


def show_unknown_tag():
    # An NFC tag was scanned that isn't in our WORKER_TAGS or BOX_TAGS lists. LED is red.
    set_led(1, 0, 0)
    show_prompt("Unknown tag")


def show_batch_running(elapsed_s):
    # Shows the screen while the worker is actively completing a box task.
    # LED is green.
    set_led(0, 1, 0)
    
    if wifi.ip():
        # ONLINE MODE: We do not show the elapsed or estimated time.
        # This keeps the worker from knowing if they are slow, reducing stress.
        show_prompt(current_box + "\nIn progress...")
    else:
        # OFFLINE MODE: We keep it exactly as it was, showing the timer and estimate.
        show_prompt(current_box + "\n" + mmss(elapsed_s) + "\nEst " + mmss(BOX_EST_SECONDS[current_box]))


def show_checkout_summary(elapsed_s, diff_s):
    # Shows a summary when a box task is finished.
    # LED is green.
    set_led(0, 1, 0)
    oled.fill(0)
    top_h = OLED_H * 5 // 8 # Calculate the height for the top text portion.
    
    if wifi.ip():
        # ONLINE MODE: Hide the exact time, and never show "Too slow".
        # Always show a success checkmark so the worker isn't stressed about time.
        draw_text_block(current_box + "\nDone", 0, top_h)
        draw_checkmark(OLED_W // 2, top_h + (OLED_H - top_h) // 2, min(10, (OLED_H - top_h) // 2 - 2))
    else:
        # OFFLINE MODE: Keep it exactly as it was, showing time and "Too slow" if they were over time.
        draw_text_block(current_box + "\n" + mmss(elapsed_s), 0, top_h)
        if diff_s > 0:
            draw_text_block("Too slow", top_h, OLED_H) # They took longer than the estimate.
        else:
            # They were faster than the estimate, show a checkmark.
            draw_checkmark(OLED_W // 2, top_h + (OLED_H - top_h) // 2, min(10, (OLED_H - top_h) // 2 - 2))
            
    oled.show()


def show_task_overview():
    # Shows a list of all completed boxes for the current worker.
    # LED is green.
    set_led(0, 1, 0)
    lines = ["Task overview"]
    for label, diff_s in batch_list:
        if wifi.ip():
            # ONLINE MODE: Always show "OK", never show "Slow", to prevent stress.
            lines.append(label + ", OK")
        else:
            # OFFLINE MODE: Keep the original behavior, showing "Slow" if applicable.
            lines.append(label + ", " + ("Slow" if diff_s > 0 else "OK"))
    show_lines(*lines)


def show_product_complete():
    # Screen shown when all boxes in the BOX_ORDER are successfully finished.
    set_led(0, 1, 0)
    oled.fill(0)
    top_h = OLED_H * 5 // 8
    draw_text_block("Product\ncompleted.", 0, top_h)
    draw_checkmark(OLED_W // 2, top_h + (OLED_H - top_h) // 2, min(10, (OLED_H - top_h) // 2 - 2))
    oled.show()


def all_boxes_done():
    # Checks if we have reached the end of the expected sequence of boxes.
    return next_box_index >= len(BOX_ORDER)


def show_checked_in_idle():
    # Determines what to show when a worker is checked in, but no box is actively being timed.
    # If they have finished some boxes, show the overview. Otherwise, just show the welcome screen.
    if batch_list:
        show_task_overview()
    else:
        show_welcome()


def wait_for_removal():
    # Prevents "double tapping" by waiting in a loop until the NFC tag is moved away from the reader.
    # The reader looks for a tag every 200ms. If it finds one, it keeps looping.
    while nfc.read_passive_target(timeout_ms=200):
        pass


def revert_display(elapsed_s=0):
    # A helper to redraw the correct screen after a temporary error message (like "Wrong Tag") disappears.
    if state == STATE_IDLE:
        show_idle()
    elif state == STATE_CHECKED_IN:
        show_checked_in_idle()
    else:
        show_batch_running(elapsed_s)


# --- Global Variables for State Tracking ---
state = STATE_IDLE      # Start in the IDLE state (waiting for worker).
box_start_ms = 0        # Stores the system time (in milliseconds) when the current box task started.
last_shown_second = -1  # Used to update the running timer only once per second.
current_worker = ""     # Name of the worker currently checked in.
current_box = ""        # Name of the box currently being timed.
next_box_index = 0      # Tracks which box in the BOX_ORDER sequence we expect next.
batch_list = []         # A list storing information about finished boxes: (box_name, time_difference).

# Initialize the display with the IDLE screen on startup.
show_idle()
print("Ready. Scan the worker tag to begin.")


def connect_wifi():
    # Attempts to connect to the WiFi network using the credentials in device_secrets.py.
    print("Connecting to WiFi...")
    ip = wifi.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
    if ip:
        print("WiFi connected:", ip)
        show_prompt("WiFi OK\n" + ip)
    else:
        # If connection fails, the device continues in OFFLINE mode (no IP address).
        print("WiFi FAILED (offline mode)")
        show_prompt("WiFi failed.\nWorking offline.")
    time.sleep(2) # Show the status message for 2 seconds.
    show_idle()   # Go back to the main IDLE screen.


# Automatically try to connect to WiFi on startup.
connect_wifi()


def sign_out():
    # Handles a worker logging off and resetting the device for the next worker.
    global state, next_box_index
    log_event("checkout") # Send the checkout event to Google Sheets (if online).
    state = STATE_IDLE
    show_goodbye()
    beep_checkout()
    time.sleep(3) # Wait 3 seconds so the worker can read the goodbye message.
    batch_list.clear() # Clear the list of completed boxes.
    next_box_index = 0 # Reset the expected box order back to the start.
    show_idle() # Return to the waiting screen.


def start_batch(label):
    # Starts the timer for a specific box.
    global state, current_box, box_start_ms, last_shown_second
    current_box = label
    state = STATE_RUNNING
    box_start_ms = time.ticks_ms() # Record the exact start time.
    last_shown_second = 0
    show_batch_running(0) # Update the display to show it's running.
    beep_scan_ok()
    log_event("box_start", box=label) # Log the start event to Google Sheets.


def checkout_box():
    # Handles when the worker scans the SAME box tag again to finish it.
    global state, next_box_index
    # Calculate how many seconds it took.
    elapsed_s = time.ticks_diff(time.ticks_ms(), box_start_ms) // 1000
    # Calculate if they were faster (negative number) or slower (positive number) than the estimate.
    diff_s = elapsed_s - BOX_EST_SECONDS[current_box]
    
    # Save this box to the worker's session summary.
    batch_list.append((current_box, diff_s))
    next_box_index += 1 # Move on to expecting the next box in the sequence.
    
    # Always log the exact data to Google Sheets, regardless of online/offline mode.
    # The sheet always gets the real status ("Slow" or "OK").
    log_event("box_done", box=current_box, elapsed_s=elapsed_s, status="Slow" if diff_s > 0 else "OK")

    show_checkout_summary(elapsed_s, diff_s)
    beep_batch_done()
    time.sleep(3) # Show the summary for 3 seconds.

    state = STATE_CHECKED_IN
    
    # Check if this was the last box in the required sequence.
    if all_boxes_done():
        show_product_complete()
        time.sleep(3)
        sign_out() # Automatically sign the worker out when they finish the whole product.
    else:
        # If there are more boxes to do, show the overview of what they've done so far.
        show_task_overview()


def checkout_worker():
    # Handles the case where a worker scans their WORKER tag while in the middle of a box task.
    # This automatically finishes the box task and checks the worker out immediately.
    elapsed_s = time.ticks_diff(time.ticks_ms(), box_start_ms) // 1000
    diff_s = elapsed_s - BOX_EST_SECONDS[current_box]
    batch_list.append((current_box, diff_s))
    
    # Send the log data, keeping it the same in the sheet regardless of display mode.
    log_event("box_done", box=current_box, elapsed_s=elapsed_s, status="Slow" if diff_s > 0 else "OK")

    show_checkout_summary(elapsed_s, diff_s)
    beep_checkout()
    time.sleep(3)
    sign_out()


# --- Main Application Loop ---
while True:
    try:
        # Check for NFC tags more frequently if we are running a timer (to ensure the screen updates).
        poll_timeout = 250 if state == STATE_RUNNING else 1000
        
        # Try to read an NFC tag. This will block (wait) until a tag is seen or the timeout expires.
        uid = nfc.read_passive_target(timeout_ms=poll_timeout)

        # ---------------------------------------------------------
        # Scenario 1: A Worker Tag was scanned
        # ---------------------------------------------------------
        if uid in WORKER_TAGS:
            name = WORKER_TAGS[uid]
            
            if state == STATE_IDLE:
                # Nobody was logged in, so log this worker in.
                current_worker = name
                state = STATE_CHECKED_IN
                show_welcome()
                beep_checkin()
                log_event("checkin")
                
            elif name != current_worker:
                # Someone else is logged in! Reject the scan.
                show_wrong_worker()
                beep_error()
                time.sleep(3)
                elapsed_s = time.ticks_diff(time.ticks_ms(), box_start_ms) // 1000
                revert_display(elapsed_s) # Go back to whatever screen was showing before the error.
                
            elif state == STATE_RUNNING:
                # The logged-in worker tapped their tag while doing a box. Check them out early.
                checkout_worker()
                
            else:
                # The logged-in worker tapped their tag while NOT doing a box. Sign them out normally.
                sign_out()
                
            wait_for_removal() # Wait for the tag to be moved away before continuing.

        # ---------------------------------------------------------
        # Scenario 2: A Box Tag was scanned
        # ---------------------------------------------------------
        elif uid in BOX_TAGS:
            label = BOX_TAGS[uid]
            
            if state == STATE_IDLE:
                # A box was scanned, but no worker is logged in yet.
                show_scan_worker_first()
                beep_error()
                time.sleep(3)
                show_idle()
                
            elif state == STATE_CHECKED_IN:
                # Worker is ready. Check if they scanned the right box in the sequence.
                expected_label = BOX_ORDER[next_box_index % len(BOX_ORDER)]
                if label == expected_label:
                    # Correct box! Start the timer.
                    start_batch(label)
                else:
                    # Wrong box! Show an error.
                    show_wrong_box_order(expected_label)
                    beep_error()
                    time.sleep(3)
                    show_checked_in_idle()
                    
            elif label == current_box:
                # They scanned the SAME box that is currently running. This means they are done.
                checkout_box()
                
            else:
                # They scanned a DIFFERENT box while one is already running. Not allowed.
                show_must_checkout_current()
                beep_error()
                time.sleep(3)
                elapsed_s = time.ticks_diff(time.ticks_ms(), box_start_ms) // 1000
                show_batch_running(elapsed_s)
                
            wait_for_removal() # Wait for the tag to be moved away.

        # ---------------------------------------------------------
        # Scenario 3: An Unknown Tag was scanned
        # ---------------------------------------------------------
        elif uid is not None:
            # We detected a tag, but it's not in any of our lists.
            show_unknown_tag()
            beep_error()
            time.sleep(2)
            elapsed_s = time.ticks_diff(time.ticks_ms(), box_start_ms) // 1000
            revert_display(elapsed_s)
            wait_for_removal()

        # ---------------------------------------------------------
        # Update the screen if a timer is running
        # ---------------------------------------------------------
        if state == STATE_RUNNING:
            elapsed_s = time.ticks_diff(time.ticks_ms(), box_start_ms) // 1000
            # Only update the screen once every second to avoid flickering.
            if elapsed_s != last_shown_second:
                last_shown_second = elapsed_s
                show_batch_running(elapsed_s)

    except KeyboardInterrupt:
        # Allow stopping the program cleanly with Ctrl+C (e.g., during development).
        break
    except Exception as e:
        # Catch any other unexpected errors so the program doesn't crash completely.
        sys.print_exception(e)
        time.sleep(1)

# Turn off hardware when the program stops.
set_led(0, 0, 0)
buzzer.duty_u16(0)
print("Stopped.")
