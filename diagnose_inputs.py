#!/usr/bin/python2
"""
Generator Input Diagnostic Script

This script runs a timer-driven generator start sequence while continuously
logging the state of all 4 optoisolated inputs on the MOD-IO board.

Purpose: Identify what generator signals are connected to IN1-IN4 before
migrating to Raspberry Pi.

Usage: python2 diagnose_inputs.py [--no-start] [--duration SECONDS]

Output: /tmp/gen_diagnostic.log (timestamped input states and relay commands)

WARNING: This script WILL attempt to start the generator unless --no-start is used.
"""

import smbus
import time
import sys
import threading
import argparse
from datetime import datetime

# I2C Configuration
BUS = smbus.SMBus(0)
MODIO_ADDR = 0x58
REG_RELAY = 0x10    # Relay control register (write)
REG_INPUT = 0x20    # Digital input register (read)

# Log file location (easy to find on SD card)
LOG_FILE = "/tmp/gen_diagnostic.log"

# Global flag to control logging thread
logging_active = True


def get_timestamp():
    """Returns current timestamp as string"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def decode_inputs(status_byte):
    """Decode input status byte into individual input states"""
    in1 = (status_byte & 0b0001) != 0
    in2 = (status_byte & 0b0010) != 0
    in3 = (status_byte & 0b0100) != 0
    in4 = (status_byte & 0b1000) != 0
    return in1, in2, in3, in4


def decode_relays(relay_byte):
    """Decode relay command byte into individual relay states"""
    starter = (relay_byte & 0b0001) != 0
    charger = (relay_byte & 0b0010) != 0
    glow = (relay_byte & 0b0100) != 0
    ignition = (relay_byte & 0b1000) != 0
    return starter, charger, glow, ignition


def format_inputs(status_byte):
    """Format input status for logging"""
    in1, in2, in3, in4 = decode_inputs(status_byte)
    return "IN1=%d IN2=%d IN3=%d IN4=%d (raw=%d, bin=%s)" % (
        in1, in2, in3, in4, status_byte, bin(status_byte)
    )


def format_relays(relay_byte):
    """Format relay state for logging"""
    starter, charger, glow, ignition = decode_relays(relay_byte)
    names = []
    if ignition: names.append("IGN")
    if glow: names.append("GLOW")
    if charger: names.append("CHARGER")
    if starter: names.append("START")
    if not names: names.append("OFF")
    return "%s (bin=%s)" % ("+".join(names), bin(relay_byte))


def log_message(logfile, message, also_print=True):
    """Write timestamped message to log file and optionally print"""
    line = "%s | %s\n" % (get_timestamp(), message)
    logfile.write(line)
    logfile.flush()
    if also_print:
        print line.strip()


def input_logger(logfile, interval=0.1):
    """Background thread that logs input states at regular intervals"""
    global logging_active
    last_status = -1

    while logging_active:
        try:
            status = BUS.read_byte_data(MODIO_ADDR, REG_INPUT)
            # Always log, but mark changes prominently
            if status != last_status:
                log_message(logfile, "INPUT CHANGE: %s" % format_inputs(status))
                last_status = status
            else:
                log_message(logfile, "INPUT: %s" % format_inputs(status), also_print=False)
        except Exception as e:
            log_message(logfile, "INPUT READ ERROR: %s" % str(e))

        time.sleep(interval)


def set_relays(logfile, relay_byte):
    """Set relay state and log the command"""
    log_message(logfile, "RELAY SET: %s" % format_relays(relay_byte))
    BUS.write_byte_data(MODIO_ADDR, REG_RELAY, relay_byte)


def run_diagnostic_sequence(logfile, run_generator=True):
    """
    Run timer-driven diagnostic sequence.

    If run_generator=False, just monitors inputs without controlling relays.
    """
    global logging_active

    log_message(logfile, "=" * 60)
    log_message(logfile, "DIAGNOSTIC SEQUENCE STARTING")
    log_message(logfile, "Generator control: %s" % ("ENABLED" if run_generator else "DISABLED (monitor only)"))
    log_message(logfile, "=" * 60)

    # Start background input logging thread
    logger_thread = threading.Thread(target=input_logger, args=(logfile, 0.1))
    logger_thread.daemon = True
    logger_thread.start()

    try:
        if not run_generator:
            # Monitor-only mode - just log inputs for 60 seconds
            log_message(logfile, "MONITOR MODE: Logging inputs for 60 seconds")
            log_message(logfile, "Manually start/stop generator to observe input changes")
            time.sleep(60)
            return

        # === PHASE 1: RESET (5 seconds baseline) ===
        log_message(logfile, "")
        log_message(logfile, "=== PHASE 1: RESET - All relays OFF (5s baseline) ===")
        set_relays(logfile, 0b0000)
        time.sleep(5)

        # === PHASE 2: IGNITION ON ===
        log_message(logfile, "")
        log_message(logfile, "=== PHASE 2: IGNITION ON (1s fuel solenoid) ===")
        set_relays(logfile, 0b1000)
        time.sleep(1)

        # === PHASE 3: INITIAL CRANK (10 seconds) ===
        log_message(logfile, "")
        log_message(logfile, "=== PHASE 3: IGNITION + STARTER (10s crank) ===")
        set_relays(logfile, 0b1001)
        time.sleep(10)

        # === PHASE 4: GLOW-ASSISTED CRANK (2 seconds) ===
        log_message(logfile, "")
        log_message(logfile, "=== PHASE 4: IGNITION + GLOW + STARTER (2s) ===")
        set_relays(logfile, 0b1101)
        time.sleep(2)

        # === PHASE 5: COAST/CHECK (4 seconds) ===
        log_message(logfile, "")
        log_message(logfile, "=== PHASE 5: IGNITION ONLY - Coast/Check (4s) ===")
        set_relays(logfile, 0b1000)
        time.sleep(4)

        # === PHASE 6: CHECK STATUS AND DECIDE ===
        log_message(logfile, "")
        status = BUS.read_byte_data(MODIO_ADDR, REG_INPUT)
        log_message(logfile, "=== STATUS CHECK: %s ===" % format_inputs(status))

        if status == 3:
            log_message(logfile, "Generator appears to be RUNNING (status=3)")

            # === PHASE 7: WARMUP (60 seconds) ===
            log_message(logfile, "")
            log_message(logfile, "=== PHASE 7: WARMUP - Ignition only (60s) ===")
            time.sleep(60)

            # === PHASE 8: ENABLE CHARGER (30 seconds to observe) ===
            log_message(logfile, "")
            log_message(logfile, "=== PHASE 8: IGNITION + CHARGER ENABLE (30s) ===")
            set_relays(logfile, 0b1010)
            time.sleep(30)

            # === PHASE 9: RUNNING OBSERVATION (30 more seconds) ===
            log_message(logfile, "")
            log_message(logfile, "=== PHASE 9: Continued running observation (30s) ===")
            time.sleep(30)
        else:
            log_message(logfile, "Generator NOT running (status=%d, expected 3)" % status)
            log_message(logfile, "Keeping ignition on for 10s to observe...")
            time.sleep(10)

        # === PHASE 10: SHUTDOWN ===
        log_message(logfile, "")
        log_message(logfile, "=== PHASE 10: SHUTDOWN - All relays OFF ===")
        set_relays(logfile, 0b0000)

        # Log for 10 more seconds to capture shutdown transition
        log_message(logfile, "Logging shutdown transition for 10s...")
        time.sleep(10)

    except KeyboardInterrupt:
        log_message(logfile, "INTERRUPTED BY USER")
    except Exception as e:
        log_message(logfile, "ERROR: %s" % str(e))
    finally:
        # Always ensure safe shutdown
        logging_active = False
        log_message(logfile, "")
        log_message(logfile, "=== EMERGENCY SHUTDOWN - All relays OFF ===")
        try:
            BUS.write_byte_data(MODIO_ADDR, REG_RELAY, 0b0000)
        except:
            pass
        log_message(logfile, "=" * 60)
        log_message(logfile, "DIAGNOSTIC SEQUENCE COMPLETE")
        log_message(logfile, "=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='Generator input diagnostic - logs input states during start sequence'
    )
    parser.add_argument(
        '--no-start',
        action='store_true',
        help='Monitor inputs only, do not control generator relays'
    )
    parser.add_argument(
        '--log-file',
        default=LOG_FILE,
        help='Log file path (default: %s)' % LOG_FILE
    )
    args = parser.parse_args()

    print "=" * 60
    print "Generator Input Diagnostic Script"
    print "=" * 60
    print ""
    print "Log file: %s" % args.log_file
    print "Generator control: %s" % ("DISABLED" if args.no_start else "ENABLED")
    print ""

    if not args.no_start:
        print "WARNING: This will attempt to START the generator!"
        print "Press Ctrl+C within 5 seconds to abort..."
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print "\nAborted."
            sys.exit(0)

    with open(args.log_file, 'w') as logfile:
        log_message(logfile, "Generator Input Diagnostic Log")
        log_message(logfile, "Script started")
        run_diagnostic_sequence(logfile, run_generator=not args.no_start)

    print ""
    print "Log saved to: %s" % args.log_file
    print "Copy this file for analysis."


if __name__ == "__main__":
    main()
