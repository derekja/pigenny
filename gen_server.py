#!/usr/bin/env python
"""
Generator Control TCP Server for Olimex MOD-IO

Compatible with Python 2.7 and Python 3.x

Listens for commands from the Pi and controls the generator via I2C relays.

Commands (newline-terminated):
  STATUS    - Returns current input states and relay states
  START     - Initiates generator start sequence
  STOP      - Stops the generator (all relays off)
  RELAY xx  - Set relay byte directly (hex, e.g., RELAY 0A)
  PING      - Returns PONG (connection test)
  QUIT      - Close connection

Responses are newline-terminated. Multi-line responses end with "END".

Usage:
  python gen_server.py [--port PORT] [--host HOST]

Default: listens on 0.0.0.0:9999
"""

from __future__ import print_function

import socket
import sys
import time
import threading
import argparse
from datetime import datetime

# Try to import smbus, but allow running without it for testing
try:
    import smbus
    BUS = smbus.SMBus(0)
    I2C_AVAILABLE = True
except:
    BUS = None
    I2C_AVAILABLE = False

# I2C Configuration
MODIO_ADDR = 0x58
REG_RELAY = 0x10
REG_INPUT = 0x20

# Server configuration
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 9999

# Generator state
generator_lock = threading.Lock()
current_relay_state = 0
generator_running = False
start_in_progress = False


def log(message):
    """Log with timestamp"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("%s | %s" % (ts, message))
    sys.stdout.flush()


def read_inputs():
    """Read input status from MOD-IO"""
    if not I2C_AVAILABLE:
        return 0  # Simulate all inputs off
    try:
        return BUS.read_byte_data(MODIO_ADDR, REG_INPUT)
    except Exception as e:
        log("I2C read error: %s" % str(e))
        return -1


def set_relays(relay_byte):
    """Set relay state"""
    global current_relay_state
    if not I2C_AVAILABLE:
        log("I2C not available, simulating relay set: 0x%02X" % relay_byte)
        current_relay_state = relay_byte
        return True
    try:
        BUS.write_byte_data(MODIO_ADDR, REG_RELAY, relay_byte)
        current_relay_state = relay_byte
        return True
    except Exception as e:
        log("I2C write error: %s" % str(e))
        return False


def format_inputs(status):
    """Format input status"""
    if status < 0:
        return "ERROR"
    in1 = (status & 0b0001) != 0
    in2 = (status & 0b0010) != 0
    in3 = (status & 0b0100) != 0
    in4 = (status & 0b1000) != 0
    return "IN1=%d IN2=%d IN3=%d IN4=%d raw=%d" % (in1, in2, in3, in4, status)


def format_relays(relay_byte):
    """Format relay state"""
    names = []
    if relay_byte & 0b1000: names.append("IGN")
    if relay_byte & 0b0100: names.append("GLOW")
    if relay_byte & 0b0010: names.append("CHARGER")
    if relay_byte & 0b0001: names.append("START")
    if not names: names.append("OFF")
    return "+".join(names)


def get_status():
    """Get full status report"""
    inputs = read_inputs()
    running = (inputs == 3)
    lines = [
        "INPUTS: %s" % format_inputs(inputs),
        "RELAYS: %s (0x%02X)" % (format_relays(current_relay_state), current_relay_state),
        "RUNNING: %s" % ("YES" if running else "NO"),
        "START_IN_PROGRESS: %s" % ("YES" if start_in_progress else "NO"),
        "I2C: %s" % ("OK" if I2C_AVAILABLE else "SIMULATED"),
        "END"
    ]
    return "\n".join(lines)


def do_start_sequence():
    """Execute generator start sequence (blocking)"""
    global start_in_progress, generator_running

    with generator_lock:
        if start_in_progress:
            return "ERROR: Start already in progress"
        start_in_progress = True

    log("Starting generator sequence...")

    try:
        # Check initial state
        inputs = read_inputs()
        if inputs == 3:
            start_in_progress = False
            return "ERROR: Generator already running"

        # Phase 1: Reset
        log("Phase 1: Reset")
        set_relays(0b0000)
        time.sleep(1)

        # Phase 2: Ignition on
        log("Phase 2: Ignition ON")
        set_relays(0b1000)
        time.sleep(0.5)

        # Phase 3: Crank (10 seconds)
        log("Phase 3: Cranking (10s)")
        set_relays(0b1001)
        time.sleep(10)

        # Phase 4: Glow + Crank (2 seconds)
        log("Phase 4: Glow + Crank (2s)")
        set_relays(0b1101)
        time.sleep(2)

        # Phase 5: Coast/Check (4 seconds)
        log("Phase 5: Coast/Check (4s)")
        set_relays(0b1000)
        time.sleep(4)

        # Check if running
        inputs = read_inputs()
        if inputs == 3:
            log("Generator RUNNING - warming up (60s)")
            time.sleep(60)
            log("Enabling charger")
            set_relays(0b1010)
            generator_running = True
            start_in_progress = False
            return "OK: Generator started and charger enabled"
        else:
            log("Start FAILED - shutting down")
            set_relays(0b0000)
            generator_running = False
            start_in_progress = False
            return "ERROR: Generator failed to start (status=%d)" % inputs

    except Exception as e:
        log("Start sequence error: %s" % str(e))
        set_relays(0b0000)
        start_in_progress = False
        return "ERROR: %s" % str(e)


def do_stop():
    """Stop the generator"""
    global generator_running
    log("Stopping generator")
    set_relays(0b0000)
    generator_running = False
    return "OK: Generator stopped"


def handle_command(cmd):
    """Process a command and return response"""
    cmd = cmd.strip().upper()
    parts = cmd.split()

    if not parts:
        return "ERROR: Empty command"

    command = parts[0]

    if command == "PING":
        return "PONG"

    elif command == "STATUS":
        return get_status()

    elif command == "START":
        # Run start in a thread so we can respond immediately
        # But for simplicity in testing, run synchronously
        return do_start_sequence()

    elif command == "STOP":
        return do_stop()

    elif command == "RELAY":
        if len(parts) < 2:
            return "ERROR: RELAY requires hex value (e.g., RELAY 0A)"
        try:
            relay_byte = int(parts[1], 16)
            if relay_byte < 0 or relay_byte > 15:
                return "ERROR: Relay value must be 0x00-0x0F"
            set_relays(relay_byte)
            return "OK: Relays set to 0x%02X (%s)" % (relay_byte, format_relays(relay_byte))
        except ValueError:
            return "ERROR: Invalid hex value"

    elif command == "INPUTS":
        inputs = read_inputs()
        return format_inputs(inputs)

    elif command == "HELP":
        return """Commands:
  PING    - Connection test (returns PONG)
  STATUS  - Full status report
  INPUTS  - Read input states only
  START   - Start generator sequence
  STOP    - Stop generator
  RELAY xx - Set relay byte (hex 00-0F)
  HELP    - This help
  QUIT    - Close connection
END"""

    elif command == "QUIT":
        return None  # Signal to close connection

    else:
        return "ERROR: Unknown command '%s' (try HELP)" % command


def handle_client(client_socket, address):
    """Handle a single client connection"""
    log("Client connected: %s:%d" % address)

    try:
        client_socket.sendall(b"GENNY SERVER READY\n")

        buffer = b""
        while True:
            data = client_socket.recv(1024)
            if not data:
                break

            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                log("Command from %s: %s" % (address[0], line))
                response = handle_command(line)

                if response is None:
                    client_socket.sendall(b"BYE\n")
                    return

                client_socket.sendall((response + "\n").encode('utf-8'))

    except socket.error as e:
        log("Socket error with %s: %s" % (address[0], str(e)))
    except Exception as e:
        log("Error with %s: %s" % (address[0], str(e)))
    finally:
        log("Client disconnected: %s:%d" % address)
        client_socket.close()


def run_server(host, port):
    """Run the TCP server"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind((host, port))
        server_socket.listen(5)
        log("Generator server listening on %s:%d" % (host, port))
        log("I2C status: %s" % ("Available" if I2C_AVAILABLE else "Not available (simulation mode)"))

        # Initialize relays to off
        set_relays(0b0000)

        while True:
            client_socket, address = server_socket.accept()
            # Handle each client in a thread
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, address)
            )
            client_thread.daemon = True
            client_thread.start()

    except KeyboardInterrupt:
        log("Server shutting down...")
    except Exception as e:
        log("Server error: %s" % str(e))
    finally:
        # Safety shutdown
        log("Emergency relay shutdown")
        set_relays(0b0000)
        server_socket.close()


def main():
    parser = argparse.ArgumentParser(description='Generator Control TCP Server')
    parser.add_argument('--host', default=DEFAULT_HOST, help='Host to bind to')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='Port to listen on')
    args = parser.parse_args()

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
