#!/usr/bin/env python3
"""
Generator Control TCP Client for Raspberry Pi

Connects to the Olimex gen_server.py and sends commands.

Usage:
  # Interactive mode:
  python3 gen_client.py --host 192.168.1.100

  # Single command:
  python3 gen_client.py --host 192.168.1.100 --cmd STATUS

  # Start generator:
  python3 gen_client.py --host 192.168.1.100 --cmd START
"""

import socket
import sys
import argparse
import time


DEFAULT_HOST = '10.2.242.109'  # Olimex static IP (or 192.168.100.2 if changed)
DEFAULT_PORT = 9999
TIMEOUT = 120  # Long timeout for START command


class GeneratorClient:
    def __init__(self, host, port, timeout=TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None

    def connect(self):
        """Connect to the generator server"""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout)
        self.socket.connect((self.host, self.port))

        # Read welcome message
        welcome = self._readline()
        return welcome

    def disconnect(self):
        """Disconnect from server"""
        if self.socket:
            try:
                self.socket.sendall(b"QUIT\n")
                self._readline()  # Read BYE
            except:
                pass
            self.socket.close()
            self.socket = None

    def _flush_receive_buffer(self):
        """Flush any stale data from the receive buffer.

        This prevents issues where old STATUS responses are read
        instead of the expected command response.
        """
        self.socket.setblocking(False)
        flushed_bytes = 0
        try:
            while True:
                try:
                    data = self.socket.recv(1024)
                    if not data:
                        break
                    flushed_bytes += len(data)
                except BlockingIOError:
                    # No more data available
                    break
                except socket.error:
                    break
        finally:
            self.socket.setblocking(True)
            self.socket.settimeout(self.timeout)

        if flushed_bytes > 0:
            import sys
            print(f"Warning: Flushed {flushed_bytes} stale bytes from TCP buffer", file=sys.stderr)

        return flushed_bytes

    def _readline(self):
        """Read a line from the socket"""
        data = b""
        while True:
            chunk = self.socket.recv(1)
            if not chunk:
                break
            if chunk == b"\n":
                break
            data += chunk
        return data.decode('utf-8').strip()

    def _read_until_end(self):
        """Read multiple lines until END marker"""
        lines = []
        while True:
            line = self._readline()
            if line == "END":
                break
            lines.append(line)
        return "\n".join(lines)

    def send_command(self, cmd):
        """Send a command and get response"""
        self.socket.sendall((cmd.strip() + "\n").encode('utf-8'))

        # Read response
        response = self._readline()

        # Multi-line response?
        if response.startswith("INPUTS:") or response.startswith("Commands:"):
            # Read until END
            rest = self._read_until_end()
            return response + "\n" + rest

        return response

    def status(self):
        """Get generator status"""
        return self.send_command("STATUS")

    def reconnect(self):
        """Close and reopen the connection to clear any queued commands."""
        try:
            if self.socket:
                self.socket.close()
        except:
            pass
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout)
        self.socket.connect((self.host, self.port))
        # Read welcome message
        self._readline()

    def start(self):
        """Start the generator.

        Reconnects first to ensure no stale/queued commands exist on the
        server side (previous timed-out STARTs could still be queued).
        Also flushes receive buffer for safety.
        """
        self.reconnect()
        self._flush_receive_buffer()
        return self.send_command("START")

    def stop(self):
        """Stop the generator.

        Flushes receive buffer first to ensure we get the actual response.
        """
        self._flush_receive_buffer()
        return self.send_command("STOP")

    def ping(self):
        """Test connection"""
        return self.send_command("PING")

    def inputs(self):
        """Read input states"""
        return self.send_command("INPUTS")

    def relay(self, value):
        """Set relay state directly (hex string or int)"""
        if isinstance(value, int):
            value = "%02X" % value
        return self.send_command("RELAY %s" % value)

    def is_running(self, debounce_checks=3, debounce_interval=2):
        """Check if generator is running with debouncing.

        After fuel-out restarts, the generator may stumble briefly before
        stabilizing or dying. This debounces the check to avoid false
        "stopped unexpectedly" triggers during brief stumbles.

        Args:
            debounce_checks: Number of consecutive "not running" checks required
            debounce_interval: Seconds between checks

        Returns True if running, False only if consistently not running.
        """
        self._flush_receive_buffer()
        status = self.status()

        if "RUNNING: YES" in status:
            return True

        # First check said not running - debounce with additional checks
        import sys
        print(f"Generator check returned not running, debouncing with {debounce_checks-1} more checks...", file=sys.stderr)

        not_running_count = 1
        for i in range(debounce_checks - 1):
            time.sleep(debounce_interval)
            self._flush_receive_buffer()
            status = self.status()
            if "RUNNING: YES" in status:
                print(f"Generator now running on recheck {i+1}", file=sys.stderr)
                return True
            not_running_count += 1
            print(f"Generator still not running (check {not_running_count}/{debounce_checks})", file=sys.stderr)

        # Consistently not running across all checks
        return False


def interactive_mode(client):
    """Run interactive command mode"""
    print("Interactive mode. Type 'help' for commands, 'quit' to exit.")
    print()

    while True:
        try:
            cmd = input("genny> ").strip()
            if not cmd:
                continue

            if cmd.lower() in ('quit', 'exit', 'q'):
                break

            response = client.send_command(cmd)
            print(response)
            print()

        except KeyboardInterrupt:
            print("\nInterrupted")
            break
        except EOFError:
            break
        except socket.timeout:
            print("Timeout waiting for response")
        except Exception as e:
            print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description='Generator Control Client')
    parser.add_argument('--host', default=DEFAULT_HOST,
                        help=f'Server host (default: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'Server port (default: {DEFAULT_PORT})')
    parser.add_argument('--cmd', '-c', help='Single command to execute')
    parser.add_argument('--timeout', type=int, default=TIMEOUT,
                        help=f'Socket timeout in seconds (default: {TIMEOUT})')
    args = parser.parse_args()

    client = GeneratorClient(args.host, args.port, args.timeout)

    try:
        print(f"Connecting to {args.host}:{args.port}...")
        welcome = client.connect()
        print(f"Connected: {welcome}")
        print()

        if args.cmd:
            # Single command mode
            response = client.send_command(args.cmd)
            print(response)
        else:
            # Interactive mode
            interactive_mode(client)

    except socket.timeout:
        print(f"Connection timed out")
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"Connection refused - is gen_server.py running on {args.host}:{args.port}?")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
