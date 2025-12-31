#!/bin/bash
#
# Install Generator TCP Server to Olimex SD Card
#
# Usage: sudo ./install_server.sh /path/to/mounted/sdcard [STATIC_IP]
#
# Examples:
#   sudo ./install_server.sh /mnt/olimex
#   sudo ./install_server.sh /mnt/olimex 192.168.100.2
#

set -e

if [ -z "$1" ]; then
    echo "Usage: sudo $0 /path/to/mounted/sdcard [STATIC_IP]"
    echo ""
    echo "Examples:"
    echo "  sudo $0 /mnt/olimex                    # Keep existing IP (10.2.242.109)"
    echo "  sudo $0 /mnt/olimex 192.168.100.2      # Set new static IP"
    exit 1
fi

SDCARD="$1"
STATIC_IP="$2"
SCRIPT_DIR="$(dirname "$0")"

# Verify mount point
if [ ! -d "$SDCARD/usr/local/bin" ]; then
    echo "ERROR: $SDCARD/usr/local/bin does not exist"
    exit 1
fi

echo "Installing Generator TCP Server to: $SDCARD"
echo ""

# Copy server script
echo "Copying gen_server.py..."
cp "$SCRIPT_DIR/gen_server.py" "$SDCARD/usr/local/bin/"
chmod 755 "$SDCARD/usr/local/bin/gen_server.py"

# Create startup wrapper
echo "Creating startup wrapper..."
cat > "$SDCARD/usr/local/bin/start_gen_server.sh" << 'EOF'
#!/bin/sh
# Start generator TCP server at boot

LOG="/var/log/gen_server.log"

echo "Starting gen_server at $(date)" >> "$LOG"

# Wait for network
sleep 10

# Start server (runs in foreground, output to log)
exec /usr/bin/python2 /usr/local/bin/gen_server.py >> "$LOG" 2>&1
EOF
chmod 755 "$SDCARD/usr/local/bin/start_gen_server.sh"

# Set up rc.local to start server at boot
echo "Configuring rc.local..."
RC_LOCAL="$SDCARD/etc/rc.local"

# Create rc.local if it doesn't exist
if [ ! -f "$RC_LOCAL" ]; then
    cat > "$RC_LOCAL" << 'EOF'
#!/bin/sh
# rc.local - executed at end of each multiuser runlevel

EOF
fi

# Remove old diagnostic entry if present
if grep -q "run_diagnostic_at_boot" "$RC_LOCAL"; then
    grep -v "run_diagnostic_at_boot" "$RC_LOCAL" > "$RC_LOCAL.tmp"
    mv "$RC_LOCAL.tmp" "$RC_LOCAL"
fi

# Remove old server entry if present
if grep -q "start_gen_server" "$RC_LOCAL"; then
    grep -v "start_gen_server" "$RC_LOCAL" > "$RC_LOCAL.tmp"
    mv "$RC_LOCAL.tmp" "$RC_LOCAL"
fi

# Add server startup (before exit 0 if present)
if grep -q "^exit 0" "$RC_LOCAL"; then
    sed -i 's|^exit 0|/usr/local/bin/start_gen_server.sh \&\nexit 0|' "$RC_LOCAL"
else
    echo '/usr/local/bin/start_gen_server.sh &' >> "$RC_LOCAL"
    echo 'exit 0' >> "$RC_LOCAL"
fi
chmod 755 "$RC_LOCAL"

echo ""
echo "rc.local contents:"
cat "$RC_LOCAL"

# Create log file location
mkdir -p "$SDCARD/var/log"

# Configure static IP if specified
if [ -n "$STATIC_IP" ]; then
    echo ""
    echo "Configuring static IP: $STATIC_IP"
    NETWORK_FILE="$SDCARD/etc/systemd/network/eth0.network"

    cat > "$NETWORK_FILE" << EOF
[Match]
Name=eth0

[Network]
DHCP=no

[Address]
Address=${STATIC_IP}/24
EOF

    echo "Network configuration:"
    cat "$NETWORK_FILE"
else
    echo ""
    echo "Keeping existing network configuration:"
    cat "$SDCARD/etc/systemd/network/eth0.network" 2>/dev/null || echo "(no config found)"
fi

# Clean up old diagnostic cron if present
CRONTAB="$SDCARD/var/spool/cron/root"
if [ -f "$CRONTAB" ]; then
    if grep -q "run_diagnostic_at_boot" "$CRONTAB"; then
        echo ""
        echo "Removing old diagnostic cron entry..."
        grep -v "run_diagnostic_at_boot" "$CRONTAB" > "$CRONTAB.tmp" || true
        mv "$CRONTAB.tmp" "$CRONTAB"
    fi
fi

echo ""
echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo ""
echo "Files installed:"
echo "  $SDCARD/usr/local/bin/gen_server.py"
echo "  $SDCARD/usr/local/bin/start_gen_server.sh"
echo ""
echo "The server will start automatically on boot."
echo "It listens on port 9999 for TCP connections."
# Determine what IP to show in instructions
if [ -n "$STATIC_IP" ]; then
    OLIMEX_IP="$STATIC_IP"
    PI_IP="${STATIC_IP%.*}.1"  # Same subnet, .1
else
    OLIMEX_IP="10.2.242.109"
    PI_IP="10.2.242.1"
fi

echo ""
echo "NEXT STEPS:"
echo "  1. Unmount: sudo umount $SDCARD"
echo "  2. Put SD card in Olimex board"
echo "  3. Direct-wire Ethernet from Pi to Olimex"
echo "  4. On Pi, set static IP on the Ethernet interface:"
echo "     sudo ip addr add ${PI_IP}/24 dev eth0"
echo "  5. Power on Olimex"
echo "  6. Test from Pi:"
echo "     python3 gen_client.py --host ${OLIMEX_IP}"
echo ""
echo "IP Addresses:"
echo "  Olimex: ${OLIMEX_IP}"
echo "  Pi:     ${PI_IP} (set this manually on Pi)"
echo ""
echo "Server commands: PING, STATUS, START, STOP, RELAY xx, HELP"
echo ""
echo "Log file on Olimex: /var/log/gen_server.log"
echo ""
