import network
import time

_wlan = None


def connect(ssid, password, timeout_s=15):
    # returns the device's IP on success, or None if it couldn't connect in time
    global _wlan
    wlan = network.WLAN(network.STA_IF)
    
    # Workaround for "[CYW43] core not in reset" error during soft reboots
    wlan.active(False)
    time.sleep_ms(200)
    
    wlan.active(True)
    _wlan = wlan
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_s * 1000:
                return None
            time.sleep_ms(200)
    return wlan.ifconfig()[0]


def ip():
    # live check (not cached) - returns the current IP if connected right now, else None
    if _wlan and _wlan.isconnected():
        return _wlan.ifconfig()[0]
    return None
