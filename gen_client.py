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

    def start(self):
        """Start the generator"""
        return self.send_command("START")

    def stop(self):
        """Stop the generator"""
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

    def is_running(self):
        """Check if generator is running"""
        status = self.status()
        return "RUNNING: YES" in status


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
