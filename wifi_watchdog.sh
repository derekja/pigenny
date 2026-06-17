#!/bin/bash
# WiFi connectivity watchdog for Raspberry Pi
# Checks if WiFi is connected; if not, attempts reconnect.
# After 8 consecutive hourly failures with generator off, reboots (once per day).

STATE_FILE="/tmp/wifi_watchdog_state"
REBOOT_MARKER="/tmp/wifi_watchdog_rebooted_today"
GENERATOR_STATUS_SCRIPT="/home/derekja/pigenny/genserverstatus.py"
GENERATOR_HOST="10.2.242.109"

# Clean up reboot marker if it's from a previous day
if [ -f "$REBOOT_MARKER" ]; then
    marker_date=$(date -r "$REBOOT_MARKER" +%Y%m%d)
    today=$(date +%Y%m%d)
    if [ "$marker_date" != "$today" ]; then
        rm -f "$REBOOT_MARKER"
    fi
fi

# Check if WiFi has an IP address and can reach the default gateway
wifi_ok() {
    local ip
    ip=$(nmcli -t -f IP4.ADDRESS device show wlan0 2>/dev/null | head -1 | cut -d: -f2)
    if [ -z "$ip" ]; then
        return 1
    fi
    local gw
    gw=$(nmcli -t -f IP4.GATEWAY device show wlan0 2>/dev/null | head -1 | cut -d: -f2)
    if [ -n "$gw" ]; then
        ping -c 1 -W 5 "$gw" >/dev/null 2>&1
        return $?
    fi
    return 1
}

# Check if generator is running via genserverstatus.py
generator_running() {
    local status
    status=$(python3 "$GENERATOR_STATUS_SCRIPT" --host "$GENERATOR_HOST" --format kv 2>/dev/null | grep "^running=" | cut -d= -f2)
    [ "$status" = "True" ] || [ "$status" = "true" ] || [ "$status" = "1" ]
}

# If WiFi is fine, reset failure counter and exit
if wifi_ok; then
    echo "0" > "$STATE_FILE"
    exit 0
fi

# WiFi is down — attempt reconnect
logger -t wifi-watchdog "WiFi down, attempting reconnect"
nmcli device disconnect wlan0 2>/dev/null
sleep 2
nmcli device connect wlan0 2>/dev/null
sleep 10

# Check if reconnect worked
if wifi_ok; then
    logger -t wifi-watchdog "WiFi reconnected successfully"
    echo "0" > "$STATE_FILE"
    exit 0
fi

# Reconnect failed — increment failure counter
failures=0
if [ -f "$STATE_FILE" ]; then
    failures=$(cat "$STATE_FILE" 2>/dev/null)
    failures=${failures:-0}
fi
failures=$((failures + 1))
echo "$failures" > "$STATE_FILE"
logger -t wifi-watchdog "WiFi reconnect failed (attempt $failures/8)"

# After 8 consecutive failures, consider reboot
if [ "$failures" -ge 8 ]; then
    # Don't reboot more than once per day
    if [ -f "$REBOOT_MARKER" ]; then
        logger -t wifi-watchdog "Already rebooted today, skipping"
        exit 1
    fi

    # Don't reboot if generator is running
    if generator_running; then
        logger -t wifi-watchdog "Generator is running, skipping reboot"
        exit 1
    fi

    logger -t wifi-watchdog "8 consecutive failures, generator off, rebooting"
    touch "$REBOOT_MARKER"
    sync
    /sbin/reboot
fi

exit 1
