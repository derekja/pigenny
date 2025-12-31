#!/bin/bash
#
# Install Generator Diagnostic Scripts to Olimex SD Card
#
# Usage: sudo ./install_diagnostic.sh /path/to/mounted/sdcard
#
# Example: sudo ./install_diagnostic.sh /mnt/olimex
#

set -e

if [ -z "$1" ]; then
    echo "Usage: sudo $0 /path/to/mounted/sdcard"
    echo ""
    echo "Example: sudo $0 /mnt/olimex"
    exit 1
fi

SDCARD="$1"
SCRIPT_DIR="$(dirname "$0")"

# Verify mount point
if [ ! -d "$SDCARD/usr/local/bin" ]; then
    echo "ERROR: $SDCARD/usr/local/bin does not exist"
    exit 1
fi

echo "Installing to: $SDCARD"
echo ""

# Copy diagnostic scripts
echo "Copying diagnose_inputs.py..."
cp "$SCRIPT_DIR/diagnose_inputs.py" "$SDCARD/usr/local/bin/"
chmod 755 "$SDCARD/usr/local/bin/diagnose_inputs.py"

echo "Copying read_inputs.py..."
cp "$SCRIPT_DIR/read_inputs.py" "$SDCARD/usr/local/bin/"
chmod 755 "$SDCARD/usr/local/bin/read_inputs.py"

echo "Copying run_diagnostic_at_boot..."
cp "$SCRIPT_DIR/run_diagnostic_at_boot" "$SDCARD/usr/local/bin/"
chmod 755 "$SDCARD/usr/local/bin/run_diagnostic_at_boot"

# Set up cron @reboot job
echo "Setting up cron @reboot job..."
CRON_DIR="$SDCARD/var/spool/cron"
mkdir -p "$CRON_DIR"

# Check for root crontab
CRONTAB="$CRON_DIR/root"
if [ -f "$CRONTAB" ]; then
    # Remove old entry if exists
    grep -v "run_diagnostic_at_boot" "$CRONTAB" > "$CRONTAB.tmp" || true
    mv "$CRONTAB.tmp" "$CRONTAB"
fi

# Add @reboot entry
echo "@reboot /usr/local/bin/run_diagnostic_at_boot" >> "$CRONTAB"
chmod 600 "$CRONTAB"

echo "Crontab entry added:"
cat "$CRONTAB"

# Also try rc.local as backup
echo ""
echo "Setting up rc.local as backup..."
RC_LOCAL="$SDCARD/etc/rc.local"

# Create rc.local if it doesn't exist
if [ ! -f "$RC_LOCAL" ]; then
    echo '#!/bin/sh' > "$RC_LOCAL"
    echo '# rc.local - executed at end of each multiuser runlevel' >> "$RC_LOCAL"
    echo '' >> "$RC_LOCAL"
fi

# Check if our line is already there
if ! grep -q "run_diagnostic_at_boot" "$RC_LOCAL"; then
    # Add before 'exit 0' if present, otherwise at end
    if grep -q "^exit 0" "$RC_LOCAL"; then
        sed -i 's|^exit 0|/usr/local/bin/run_diagnostic_at_boot \&\nexit 0|' "$RC_LOCAL"
    else
        echo '/usr/local/bin/run_diagnostic_at_boot &' >> "$RC_LOCAL"
        echo 'exit 0' >> "$RC_LOCAL"
    fi
fi
chmod 755 "$RC_LOCAL"

echo "rc.local contents:"
cat "$RC_LOCAL"

# Clean up old systemd service if present
if [ -f "$SDCARD/etc/systemd/system/gen-diagnostic.service" ]; then
    echo ""
    echo "Removing old systemd service..."
    rm -f "$SDCARD/etc/systemd/system/gen-diagnostic.service"
    rm -f "$SDCARD/etc/systemd/system/multi-user.target.wants/gen-diagnostic.service"
fi

# Create log directory
mkdir -p "$SDCARD/var/log"

echo ""
echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo ""
echo "Files installed:"
echo "  $SDCARD/usr/local/bin/diagnose_inputs.py"
echo "  $SDCARD/usr/local/bin/read_inputs.py"
echo "  $SDCARD/usr/local/bin/run_diagnostic_at_boot"
echo ""
echo "Boot triggers:"
echo "  - cron @reboot job (primary)"
echo "  - rc.local (backup)"
echo ""
echo "NEXT STEPS:"
echo "  1. Unmount: sudo umount $SDCARD"
echo "  2. Put SD card in Olimex board"
echo "  3. Connect to generator (key in OFF position)"
echo "  4. Apply power - diagnostic runs after ~30s"
echo "  5. Wait ~3 minutes for sequence to complete"
echo "  6. Remove power, retrieve SD card"
echo ""
echo "DEBUG FILES TO CHECK:"
echo "  $SDCARD/var/log/diag_cron_ran.txt    <- Proves boot script ran"
echo "  $SDCARD/var/log/gen_diag_started.txt <- Proves Python started"
echo "  $SDCARD/var/log/gen_diagnostic.log   <- Full diagnostic log"
echo ""
echo "KEY POSITION: Leave key in OFF position!"
echo ""
