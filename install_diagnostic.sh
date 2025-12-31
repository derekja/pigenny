#!/bin/bash
#
# Install Generator Diagnostic Scripts to Olimex SD Card
#
# Usage: sudo ./install_diagnostic.sh /path/to/mounted/sdcard
#
# Example: sudo ./install_diagnostic.sh /mnt/olimex
#
# This script copies the diagnostic files to the SD card and enables
# the systemd service to run automatically on boot.
#

set -e

if [ -z "$1" ]; then
    echo "Usage: sudo $0 /path/to/mounted/sdcard"
    echo ""
    echo "Example: sudo $0 /mnt/olimex"
    echo ""
    echo "First mount the SD card:"
    echo "  sudo mkdir -p /mnt/olimex"
    echo "  sudo mount /dev/sdX2 /mnt/olimex    # Replace sdX2 with actual device"
    exit 1
fi

SDCARD="$1"
SCRIPT_DIR="$(dirname "$0")"

# Verify mount point looks like an Arch Linux root filesystem
if [ ! -d "$SDCARD/usr/local/bin" ]; then
    echo "ERROR: $SDCARD does not appear to be a valid root filesystem"
    echo "Missing: $SDCARD/usr/local/bin"
    exit 1
fi

if [ ! -d "$SDCARD/etc/systemd/system" ]; then
    echo "ERROR: $SDCARD does not appear to have systemd"
    echo "Missing: $SDCARD/etc/systemd/system"
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

# Copy and enable systemd service
echo "Copying gen-diagnostic.service..."
cp "$SCRIPT_DIR/gen-diagnostic.service" "$SDCARD/etc/systemd/system/"
chmod 644 "$SDCARD/etc/systemd/system/gen-diagnostic.service"

echo "Enabling service (creating symlink)..."
mkdir -p "$SDCARD/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/gen-diagnostic.service \
    "$SDCARD/etc/systemd/system/multi-user.target.wants/gen-diagnostic.service"

# Create log directory
echo "Creating /var/log directory if needed..."
mkdir -p "$SDCARD/var/log"

echo ""
echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo ""
echo "Files installed:"
echo "  $SDCARD/usr/local/bin/diagnose_inputs.py"
echo "  $SDCARD/usr/local/bin/read_inputs.py"
echo "  $SDCARD/etc/systemd/system/gen-diagnostic.service"
echo ""
echo "Service enabled: gen-diagnostic.service"
echo ""
echo "On boot, the diagnostic will:"
echo "  1. Wait 30 seconds for system to stabilize"
echo "  2. Run the full generator start sequence"
echo "  3. Log all input states to /var/log/gen_diagnostic.log"
echo ""
echo "NEXT STEPS:"
echo "  1. Unmount: sudo umount $SDCARD"
echo "  2. Put SD card in Olimex board"
echo "  3. Connect to generator"
echo "  4. Apply power - diagnostic runs automatically"
echo "  5. Wait ~4 minutes for sequence to complete"
echo "  6. Remove power, retrieve SD card"
echo "  7. Mount SD card again and read: /var/log/gen_diagnostic.log"
echo ""
