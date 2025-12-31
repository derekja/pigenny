#!/usr/bin/python2
"""
Generator Input Diagnostic Script v2

Changes from v1:
- Writes heartbeat file immediately to prove script ran
- Better error handling and logging
- Writes to multiple log locations
- Delays I2C init to capture early errors
"""

import time
import sys
import os
import traceback
from datetime import datetime

# Log file locations - try multiple places
LOG_LOCATIONS = [
    "/var/log/gen_diagnostic.log",
    "/tmp/gen_diagnostic.log",
    "/home/alarm/gen_diagnostic.log",
    "/root/gen_diagnostic.log",
]

# Heartbeat file - proves script started
HEARTBEAT_FILE = "/var/log/gen_diag_started.txt"
HEARTBEAT_BACKUP = "/tmp/gen_diag_started.txt"

# Global log file handle
logfile = None


def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def write_heartbeat(message):
    """Write heartbeat to prove script is running"""
    ts = get_timestamp()
    content = "%s | %s\n" % (ts, message)

    for path in [HEARTBEAT_FILE, HEARTBEAT_BACKUP]:
        try:
            with open(path, 'a') as f:
                f.write(content)
        except:
            pass


def open_log():
    """Try to open log file in multiple locations"""
    global logfile

    for path in LOG_LOCATIONS:
        try:
            logfile = open(path, 'w')
            write_heartbeat("Log opened: %s" % path)
            return path
        except Exception as e:
            write_heartbeat("Failed to open %s: %s" % (path, str(e)))

    return None


def log(message, also_print=True):
    """Write to log file"""
    global logfile
    ts = get_timestamp()
    line = "%s | %s" % (ts, message)

    if also_print:
        print line

    if logfile:
        try:
            logfile.write(line + "\n")
            logfile.flush()
        except:
            pass

    # Also write important messages to heartbeat
    if "ERROR" in message or "PHASE" in message or "CHANGE" in message:
        write_heartbeat(message)


def init_i2c():
    """Initialize I2C bus with error handling"""
    log("Attempting to import smbus...")
    try:
        import smbus
        log("smbus imported successfully")
    except ImportError as e:
        log("ERROR: Failed to import smbus: %s" % str(e))
        return None, None

    log("Attempting to open I2C bus 0...")
    try:
        bus = smbus.SMBus(0)
        log("I2C bus 0 opened successfully")
    except Exception as e:
        log("ERROR: Failed to open I2C bus 0: %s" % str(e))
        return None, None

    # Test communication with MOD-IO
    log("Testing communication with MOD-IO at 0x58...")
    try:
        status = bus.read_byte_data(0x58, 0x20)
        log("MOD-IO responded! Initial input status: %d (bin: %s)" % (status, bin(status)))
        return bus, smbus
    except Exception as e:
        log("ERROR: MOD-IO communication failed: %s" % str(e))
        return None, None


def format_inputs(status):
    """Format input status for logging"""
    if status is None:
        return "READ_ERROR"
    in1 = (status & 0b0001) != 0
    in2 = (status & 0b0010) != 0
    in3 = (status & 0b0100) != 0
    in4 = (status & 0b1000) != 0
    return "IN1=%d IN2=%d IN3=%d IN4=%d (raw=%d bin=%s)" % (
        in1, in2, in3, in4, status, bin(status)
    )


def format_relays(relay_byte):
    """Format relay state for logging"""
    names = []
    if relay_byte & 0b1000: names.append("IGN")
    if relay_byte & 0b0100: names.append("GLOW")
    if relay_byte & 0b0010: names.append("CHARGER")
    if relay_byte & 0b0001: names.append("START")
    if not names: names.append("OFF")
    return "%s (bin=%s)" % ("+".join(names), bin(relay_byte))


def read_inputs(bus):
    """Read input status with error handling"""
    try:
        return bus.read_byte_data(0x58, 0x20)
    except Exception as e:
        log("ERROR reading inputs: %s" % str(e))
        return None


def set_relays(bus, relay_byte):
    """Set relay state with error handling"""
    log("RELAY SET: %s" % format_relays(relay_byte))
    try:
        bus.write_byte_data(0x58, 0x10, relay_byte)
        return True
    except Exception as e:
        log("ERROR setting relays: %s" % str(e))
        return False


def monitor_inputs(bus, duration, interval=0.1):
    """Monitor inputs for a duration, logging changes"""
    log("Monitoring inputs for %d seconds..." % duration)
    last_status = -1
    end_time = time.time() + duration

    while time.time() < end_time:
        status = read_inputs(bus)
        if status != last_status:
            log("INPUT CHANGE: %s" % format_inputs(status))
            last_status = status
        time.sleep(interval)

    return last_status


def run_diagnostic(bus):
    """Run the diagnostic sequence"""

    log("")
    log("=" * 60)
    log("GENERATOR INPUT DIAGNOSTIC STARTING")
    log("=" * 60)
    log("")

    # Check initial state
    initial = read_inputs(bus)
    log("Initial input state: %s" % format_inputs(initial))
    log("")

    # === PHASE 1: BASELINE (10 seconds) ===
    log("=== PHASE 1: BASELINE - All relays OFF (10s) ===")
    set_relays(bus, 0b0000)
    last = monitor_inputs(bus, 10)
    log("End of baseline. Status: %s" % format_inputs(last))
    log("")

    # === PHASE 2: IGNITION ONLY (5 seconds) ===
    log("=== PHASE 2: IGNITION ON (5s) ===")
    log("This energizes the fuel solenoid")
    set_relays(bus, 0b1000)
    last = monitor_inputs(bus, 5)
    log("End of ignition-only. Status: %s" % format_inputs(last))
    log("")

    # === PHASE 3: CRANK (10 seconds) ===
    log("=== PHASE 3: IGNITION + STARTER (10s crank) ===")
    set_relays(bus, 0b1001)
    last = monitor_inputs(bus, 10)
    log("End of crank. Status: %s" % format_inputs(last))
    log("")

    # === PHASE 4: GLOW + CRANK (2 seconds) ===
    log("=== PHASE 4: IGNITION + GLOW + STARTER (2s) ===")
    set_relays(bus, 0b1101)
    last = monitor_inputs(bus, 2)
    log("End of glow-crank. Status: %s" % format_inputs(last))
    log("")

    # === PHASE 5: COAST (5 seconds) ===
    log("=== PHASE 5: IGNITION ONLY - Coast (5s) ===")
    set_relays(bus, 0b1000)
    last = monitor_inputs(bus, 5)
    log("End of coast. Status: %s" % format_inputs(last))
    log("")

    # === PHASE 6: CHECK STATUS ===
    log("=== PHASE 6: STATUS CHECK ===")
    status = read_inputs(bus)
    log("Current status: %s" % format_inputs(status))

    if status == 3:
        log("Generator appears RUNNING (status=3)")
        log("")

        # === PHASE 7: WARMUP (30 seconds) ===
        log("=== PHASE 7: WARMUP (30s) ===")
        last = monitor_inputs(bus, 30)
        log("")

        # === PHASE 8: CHARGER ENABLE (30 seconds) ===
        log("=== PHASE 8: IGNITION + CHARGER (30s) ===")
        set_relays(bus, 0b1010)
        last = monitor_inputs(bus, 30)
        log("")
    else:
        log("Generator NOT running (expected status=3, got %s)" % str(status))
        log("Observing for 10 more seconds...")
        last = monitor_inputs(bus, 10)
        log("")

    # === PHASE 9: SHUTDOWN ===
    log("=== PHASE 9: SHUTDOWN ===")
    set_relays(bus, 0b0000)
    last = monitor_inputs(bus, 10)
    log("")

    log("=" * 60)
    log("DIAGNOSTIC COMPLETE")
    log("=" * 60)


def main():
    # Immediately write heartbeat to prove we started
    write_heartbeat("Script starting - diagnose_inputs.py v2")
    write_heartbeat("Python version: %s" % sys.version)
    write_heartbeat("Working directory: %s" % os.getcwd())
    write_heartbeat("Script path: %s" % os.path.abspath(__file__))

    # Open log file
    log_path = open_log()
    if log_path:
        log("Log file opened: %s" % log_path)
    else:
        write_heartbeat("ERROR: Could not open any log file!")

    log("Generator Input Diagnostic Script v2")
    log("Python: %s" % sys.version)
    log("")

    # Check for --no-start argument
    no_start = "--no-start" in sys.argv
    if no_start:
        log("MODE: Monitor only (--no-start)")
    else:
        log("MODE: Full diagnostic with generator start")

    # Initialize I2C
    bus, smbus = init_i2c()

    if bus is None:
        log("FATAL: Cannot communicate with MOD-IO")
        log("Check I2C bus and MOD-IO connection")
        write_heartbeat("FATAL: I2C/MOD-IO init failed")
        return 1

    try:
        if no_start:
            log("Monitoring inputs for 120 seconds...")
            log("Manually operate generator to observe input changes")
            monitor_inputs(bus, 120)
        else:
            run_diagnostic(bus)
    except KeyboardInterrupt:
        log("Interrupted by user")
    except Exception as e:
        log("ERROR: %s" % str(e))
        log("Traceback: %s" % traceback.format_exc())
        write_heartbeat("ERROR: %s" % str(e))
    finally:
        # Always try to shut down relays
        log("Emergency shutdown - all relays OFF")
        try:
            bus.write_byte_data(0x58, 0x10, 0b0000)
        except:
            pass

    if logfile:
        logfile.close()

    write_heartbeat("Script completed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        write_heartbeat("FATAL EXCEPTION: %s" % str(e))
        write_heartbeat("Traceback: %s" % traceback.format_exc())
        sys.exit(1)
