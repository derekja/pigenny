#!/usr/bin/env python2
"""
genserverstatus.py - Query gen_server.py status and diagnostics

This script connects to gen_server.py on port 9999 and retrieves
comprehensive status including generator state, system health, and
resource usage.

Usage:
    python2 genserverstatus.py [--host HOST] [--port PORT] [--format FORMAT]

Options:
    --host HOST       Hostname or IP (default: 127.0.0.1)
    --port PORT       Port number (default: 9999)
    --format FORMAT   Output format: human, compact, kv (default: human)
    --timeout SEC     Connection timeout (default: 5)

Output formats:
    human    - Human-readable multi-line output
    compact  - Single-line summary
    kv       - Key=value pairs (one per line)
"""

from __future__ import print_function
import socket
import sys
import argparse
import time


def query_status(host, port, timeout=5):
    """Connect to gen_server and get STATUS"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))

        # Read welcome
        welcome = s.recv(1024)
        if not welcome:
            return None, "No welcome message received"

        # Send STATUS command
        s.send(b'STATUS\n')

        # Read response until END
        data = b''
        start = time.time()
        while b'END' not in data and time.time() - start < timeout:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk

        s.close()

        if b'END' not in data:
            return None, "Incomplete response (no END marker)"

        return data.decode('utf-8', errors='ignore'), None

    except socket.timeout:
        return None, "Connection timeout"
    except socket.error as e:
        return None, "Connection error: %s" % str(e)
    except Exception as e:
        return None, "Error: %s" % str(e)


def parse_status(status_text):
    """Parse STATUS response into dictionary"""
    result = {}
    for line in status_text.split('\n'):
        line = line.strip()
        if not line or line == 'END':
            continue
        if ':' in line:
            key, value = line.split(':', 1)
            result[key.strip()] = value.strip()
    return result


def format_human(status_dict, host, port):
    """Format status as human-readable output"""
    print("=" * 60)
    print("Generator Server Status - %s:%d" % (host, port))
    print("=" * 60)
    print()

    # Generator status
    print("GENERATOR:")
    print("  Running:        %s" % status_dict.get('RUNNING', 'unknown'))
    print("  Start Progress: %s" % status_dict.get('START_IN_PROGRESS', 'unknown'))
    print("  Relays:         %s" % status_dict.get('RELAYS', 'unknown'))
    print("  Inputs:         %s" % status_dict.get('INPUTS', 'unknown'))
    print()

    # System health
    print("SYSTEM HEALTH:")
    print("  Active Threads: %s" % status_dict.get('THREADS', 'unknown'))
    print("  Uptime:         %s" % status_dict.get('UPTIME', 'unknown'))
    print("  Memory Used:    %s" % status_dict.get('MEMORY', 'unknown'))
    print("  Disk Usage:     %s" % status_dict.get('DISK', 'unknown'))
    print("  I2C Status:     %s" % status_dict.get('I2C', 'unknown'))
    print()


def format_compact(status_dict, host, port):
    """Format status as single-line summary"""
    running = status_dict.get('RUNNING', '?')
    relays = status_dict.get('RELAYS', '?').split()[0]  # Just relay names
    threads = status_dict.get('THREADS', '?')
    uptime = status_dict.get('UPTIME', '?')
    memory = status_dict.get('MEMORY', '?')

    print("[%s:%d] RUN=%s RELAY=%s THR=%s UP=%s MEM=%s" %
          (host, port, running, relays, threads, uptime, memory))


def format_kv(status_dict, host, port):
    """Format status as key=value pairs"""
    print("host=%s" % host)
    print("port=%d" % port)
    for key, value in sorted(status_dict.items()):
        # Sanitize key: lowercase, replace spaces with underscores
        clean_key = key.lower().replace(' ', '_').replace(':', '')
        print("%s=%s" % (clean_key, value))


def main():
    parser = argparse.ArgumentParser(description='Query gen_server.py status')
    parser.add_argument('--host', default='127.0.0.1',
                        help='Server hostname or IP (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=9999,
                        help='Server port (default: 9999)')
    parser.add_argument('--format', choices=['human', 'compact', 'kv'], default='human',
                        help='Output format (default: human)')
    parser.add_argument('--timeout', type=float, default=5.0,
                        help='Connection timeout in seconds (default: 5)')
    args = parser.parse_args()

    # Query status
    status_text, error = query_status(args.host, args.port, args.timeout)

    if error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 1

    # Parse response
    status_dict = parse_status(status_text)

    if not status_dict:
        print("ERROR: Failed to parse status response", file=sys.stderr)
        return 1

    # Format output
    if args.format == 'human':
        format_human(status_dict, args.host, args.port)
    elif args.format == 'compact':
        format_compact(status_dict, args.host, args.port)
    elif args.format == 'kv':
        format_kv(status_dict, args.host, args.port)

    return 0


if __name__ == '__main__':
    sys.exit(main())
