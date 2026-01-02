#!/usr/bin/env python2
"""
update_genserver.py - Update and restart gen_server.py on Olimex

This script automates the deployment of gen_server.py updates.
Must be run as root or with sudo.

Usage:
    python2 update_genserver.py [--source /path/to/gen_server.py]

Default source: /home/derekja/gen_server.py
Target: /usr/local/bin/gen_server.py
"""

from __future__ import print_function
import os
import sys
import time
import subprocess
import shutil
import argparse


def log(msg):
    """Print timestamped message"""
    print("[update_genserver] %s" % msg)
    sys.stdout.flush()


def check_root():
    """Verify running as root"""
    if os.geteuid() != 0:
        log("ERROR: Must run as root (use sudo)")
        return False
    return True


def find_gen_server_pid():
    """Find PID of running gen_server.py"""
    try:
        output = subprocess.check_output(["ps", "aux"])
        for line in output.split('\n'):
            if 'python2' in line and 'gen_server.py' in line and 'grep' not in line:
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])
    except Exception as e:
        log("Error finding gen_server PID: %s" % str(e))
    return None


def wait_for_gen_server(timeout=30):
    """Wait for gen_server to start"""
    log("Waiting for gen_server to start (timeout %ds)..." % timeout)
    start = time.time()
    while time.time() - start < timeout:
        pid = find_gen_server_pid()
        if pid:
            log("gen_server.py started with PID %d" % pid)
            return True
        time.sleep(1)
    return False


def verify_listening(port=9999, timeout=10):
    """Verify gen_server is listening on port"""
    import socket
    log("Verifying gen_server listening on port %d..." % port)
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(('127.0.0.1', port))
            s.close()
            log("gen_server is responding on port %d" % port)
            return True
        except:
            time.sleep(1)
    return False


def main():
    parser = argparse.ArgumentParser(description='Update and restart gen_server.py')
    parser.add_argument('--source', default='/home/derekja/gen_server.py',
                        help='Source file path (default: /home/derekja/gen_server.py)')
    parser.add_argument('--no-verify', action='store_true',
                        help='Skip verification checks')
    args = parser.parse_args()

    log("Starting gen_server.py update")

    # Check root
    if not check_root():
        return 1

    # Check source file exists
    if not os.path.exists(args.source):
        log("ERROR: Source file not found: %s" % args.source)
        return 1

    source_size = os.path.getsize(args.source)
    log("Source file: %s (%d bytes)" % (args.source, source_size))

    # Check target directory
    target = '/usr/local/bin/gen_server.py'
    target_dir = os.path.dirname(target)
    if not os.path.isdir(target_dir):
        log("ERROR: Target directory does not exist: %s" % target_dir)
        return 1

    # Find current PID
    old_pid = find_gen_server_pid()
    if old_pid:
        log("Found running gen_server.py (PID %d)" % old_pid)
    else:
        log("No running gen_server.py found")

    # Copy file
    log("Copying %s -> %s" % (args.source, target))
    try:
        shutil.copy2(args.source, target)
        os.chmod(target, 0o755)
        log("File copied and permissions set (755)")
    except Exception as e:
        log("ERROR: Failed to copy file: %s" % str(e))
        return 1

    # Kill old process
    if old_pid:
        log("Stopping old gen_server.py (PID %d)" % old_pid)
        try:
            os.kill(old_pid, 15)  # SIGTERM
            log("Sent SIGTERM, waiting for process to exit...")
            # Wait up to 5 seconds for graceful exit
            for i in range(50):
                try:
                    os.kill(old_pid, 0)  # Check if still alive
                    time.sleep(0.1)
                except OSError:
                    log("Process exited")
                    break
            else:
                log("Process did not exit, sending SIGKILL")
                os.kill(old_pid, 9)
        except Exception as e:
            log("Error stopping process: %s" % str(e))

    # The process should auto-restart via rc.local
    # Wait for it to come back
    if not args.no_verify:
        if not wait_for_gen_server(timeout=30):
            log("WARNING: gen_server did not restart automatically")
            log("You may need to reboot or manually start it")
            return 2

        if not verify_listening():
            log("WARNING: gen_server is running but not responding on port 9999")
            return 2

    log("Update completed successfully")
    return 0


if __name__ == '__main__':
    sys.exit(main())
