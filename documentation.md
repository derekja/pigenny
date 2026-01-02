# PiGenny - Hybrid Generator Controller

Generator autostart system for solar-powered battery backup using a Raspberry Pi 4B and Olimex iMX233-OLinuXino.

## Overview

This system monitors battery state of charge via RS-485 Modbus communication with a LuxPower/GSL inverter and automatically starts a backup generator when battery levels drop below a threshold.

### Architecture

The system uses a **hybrid architecture** with two boards communicating via direct-wire Ethernet:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              HYBRID ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────┐         ┌──────────────────────────┐         │
│  │   Raspberry Pi 4B        │  TCP    │  Olimex iMX233-OLinuXino │         │
│  │   (10.2.242.1)           │◄───────►│  (10.2.242.109)          │         │
│  │                          │  :9999  │                          │         │
│  │  - Inverter monitoring   │         │  - Generator control     │         │
│  │  - Decision logic        │         │  - I2C relay interface   │         │
│  │  - CSV data logging      │         │  - Input sensing         │         │
│  │  - RS-485 to inverter    │         │  - MOD-IO board          │         │
│  └────────────┬─────────────┘         └────────────┬─────────────┘         │
│               │                                    │                        │
│               │ RS-485                             │ I2C                    │
│               ▼                                    ▼                        │
│  ┌──────────────────────────┐         ┌──────────────────────────┐         │
│  │   LuxPower Inverter      │         │   Generator              │         │
│  │   - Battery SOC          │         │   - Relays (IGN, START,  │         │
│  │   - PV power             │         │     GLOW, CHARGER)       │         │
│  │   - Charge/discharge     │         │   - Status inputs        │         │
│  └──────────────────────────┘         └──────────────────────────┘         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Why Hybrid?

- **Olimex**: Low power (~0.5W), proven I2C relay control, optoisolated inputs already wired
- **Raspberry Pi**: Modern Python 3, easy updates, RS-485 HAT for inverter communication
- **Direct Ethernet**: Simple, reliable TCP communication between boards

---

## Network Configuration

### Default IP Addresses

| Device | IP Address | Purpose |
|--------|------------|---------|
| Olimex | 10.2.242.109 | Generator control server |
| Raspberry Pi | 10.2.242.1 | Monitor and control client |

The Pi and Olimex are connected via **direct-wire Ethernet** (no switch/router needed).

### Setting Pi Static IP (Temporary)

```bash
sudo ip addr add 10.2.242.1/24 dev eth0
```

### Setting Pi Static IP (Persistent)

To make the Pi's IP address persist across reboots, edit `/etc/dhcpcd.conf`:

```bash
sudo nano /etc/dhcpcd.conf
```

Add at the end:

```
# Static IP for direct connection to Olimex
interface eth0
static ip_address=10.2.242.1/24
nolink
```

The `nolink` option prevents dhcpcd from waiting for a link on eth0 if the Olimex isn't connected.

Then restart networking:

```bash
sudo systemctl restart dhcpcd
```

### Verifying Network Connection

```bash
# From Pi - ping the Olimex
ping 10.2.242.109

# Test TCP connection
nc -zv 10.2.242.109 9999
```

### SSH Access to Olimex

The Olimex has SSH enabled with the following credentials:

| User | Password | Notes |
|------|----------|-------|
| `derekja` | `Login123` | Regular user with sudo access |
| `root` | (may vary) | Root password may have been changed |

**Recommended**: Use the `derekja` account:

```bash
ssh derekja@10.2.242.109
```

The `derekja` user is a member of the `wheel` group and can run sudo commands.

**Note**: This system is on an isolated direct-wire network and not exposed to the internet. These credentials are documented for convenience.

---

## File Structure

```
pigenny/
├── documentation.md        # This file
├── CLAUDE.md               # AI assistant context documentation
├── gen_server.py           # TCP server for Olimex (Python 2/3 compatible)
├── gen_client.py           # TCP client for Pi (Python 3)
├── monitor.py              # Main monitoring daemon with auto-start logic
├── update_genserver.py     # Automated deployment tool for Olimex (Python 2)
├── genserverstatus.py      # Status query tool (Python 2/3 compatible)
├── install_server.sh       # Script to install server to Olimex SD card
└── luxpower_485/           # Inverter communication tools
    └── dump_ongoing.py     # Standalone inverter data logger
```

### Installed Locations

**On Raspberry Pi:**
- `/home/derekja/pigenny/monitor.py` - Main control daemon
- `/home/derekja/pigenny/genserverstatus.py` - Status query tool
- `/etc/systemd/system/pigenny.service` - Systemd service file
- `/var/log/pigenny/data_YYYYMMDD.csv` - Daily CSV logs

**On Olimex:**
- `/usr/local/bin/gen_server.py` - Generator control server
- `/usr/local/bin/update_genserver.py` - Update automation tool
- `/usr/local/bin/genserverstatus.py` - Status query tool
- `/usr/local/bin/start_gen_server.sh` - Auto-start wrapper script
- `/etc/rc.local` - Calls start_gen_server.sh at boot
- `/var/log/gen_server.log` - Server log file

---

## Olimex Generator Server (gen_server.py)

The Olimex runs a TCP server that accepts commands to control the generator.

### Commands

| Command | Description | Response |
|---------|-------------|----------|
| `PING` | Connection test | `PONG` |
| `STATUS` | Full status report | Multi-line status |
| `INPUTS` | Read input states only | `IN1=x IN2=x IN3=x IN4=x raw=x` |
| `START` | Start generator sequence | `OK: Generator started...` or `ERROR: ...` |
| `STOP` | Stop generator (with cooldown) | `OK: Generator stopped...` |
| `RELAY xx` | Set relay byte directly (hex) | `OK: Relays set to 0xXX` |
| `HELP` | List commands | Command list |
| `QUIT` | Close connection | `BYE` |

### Server Port

The server listens on **port 9999** on all interfaces.

---

## Relay Assignments

| Relay | Bit | Hex | Function | Notes |
|-------|-----|-----|----------|-------|
| REL1 | 0 | 0x01 | Starter Motor | Cranking relay |
| REL2 | 1 | 0x02 | Charger Enable | 45EG20AG coil (24VAC) |
| REL3 | 2 | 0x04 | Glow Plugs | Diesel preheat |
| REL4 | 3 | 0x08 | Ignition/Run/Fuel | Main run enable |

### Common Relay States

| State | Binary | Hex | Active Relays |
|-------|--------|-----|---------------|
| OFF | 0b0000 | 0x00 | None |
| IGN only | 0b1000 | 0x08 | REL4 (Ignition) |
| Cranking | 0b1001 | 0x09 | REL4 + REL1 (IGN + Starter) |
| Glow+Crank | 0b1101 | 0x0D | REL4 + REL3 + REL1 |
| Running+Charger | 0b1010 | 0x0A | REL4 + REL2 (IGN + Charger) |

---

## Generator Start Sequence

### Timing Diagram

```
Time(s)  Relays           Binary   Action
─────────────────────────────────────────────────────────────────
  0.0    All OFF          0b0000   RESET: All relays off
  1.0    IGN              0b1000   IGNITION ON (fuel solenoid)

  1.5    IGN+START        0b1001   Begin cranking
 11.5                              (10 second crank attempt)

 11.5    IGN+GLOW+START   0b1101   Add glow plugs
 13.5                              (2 second glow-assisted crank)

 13.5    IGN              0b1000   Release starter, coast
 17.5                              (4 second coast/check)

 17.5    Check status (inputs == 3?)
         │
         ├─ IF RUNNING (status == 3):
         │   17.5-77.5   IGN        0b1000   60s warmup
         │   77.5-97.5   IGN        0b1000   20s AC stabilization ◄── NEW
         │   97.5+       IGN+CHRG   0b1010   Enable charger (REL2)
         │               Return "OK: Generator started and charger enabled"
         │
         └─ IF NOT RUNNING:
             All OFF      0b0000   Emergency shutdown
             Return "ERROR: Generator failed to start"
```

### Key Timing Notes

- **Charger Enable Delay**: 20 seconds after generator shows RUNNING status
  - Allows AC voltage and frequency to fully stabilize
  - Protects charger from power fluctuations
  - Total time from running detection to charger: 60s warmup + 20s stabilization = 80s

---

## Generator Stop Sequence (Cooldown)

When STOP is issued, the generator goes through a **3-minute cooldown**:

```
Time(s)  Relays      Binary   Action
─────────────────────────────────────────────────────────────
  0.0    IGN         0b1000   Disconnect charger (REL2 OFF)
                              Keep ignition on, engine runs at idle

  0-180  IGN         0b1000   3 minute idle cooldown
                              (allows engine to cool gradually)

180.0    All OFF     0b0000   Full shutdown
                              (fuel solenoid closes, engine stops)
```

### Why Cooldown?

- Prevents thermal shock to turbocharger (if equipped)
- Allows engine oil to circulate and cool bearings
- Reduces wear on engine components
- Standard practice for diesel generators

---

## Generator Status Inputs

The Olimex MOD-IO reads generator status via 4 optoisolated inputs.

### Input Mapping (Confirmed)

| Input | Wire Color | Function | Behavior |
|-------|------------|----------|----------|
| IN1 | Blue-black | Engine Running | HIGH when engine catches |
| IN2 | Blue | Ignition Active | HIGH when IGN relay energized |
| IN3 | Blue-white | Unused | Never activated |
| IN4 | Green-black | Starter Engaged | HIGH while cranking |

### Status Byte Values

| Value | Binary | Inputs Active | Generator State |
|-------|--------|---------------|-----------------|
| 0 | 0b0000 | None | Off/Idle |
| 2 | 0b0010 | IN2 | Ignition on, not running |
| 3 | 0b0011 | IN1 + IN2 | **RUNNING** |
| 10 | 0b1010 | IN2 + IN4 | Cranking |
| 11 | 0b1011 | IN1 + IN2 + IN4 | Engine catching |

The code checks `status == 3` to confirm the generator has started successfully.

---

## Pi Monitor (monitor.py)

The monitor runs on the Raspberry Pi and:
1. Reads battery SOC from the inverter via RS-485 Modbus
2. Sends START/STOP commands to the Olimex via TCP
3. Logs data to CSV files

### Command Line Options

```
usage: monitor.py [-h] [--test-inverter] [--test-generator]
                  [--inverter-port PORT] [--inverter-baud BAUD]
                  [--generator-host HOST] [--soc-start PCT] [--soc-stop PCT]
                  [--log-dir DIR] [--log-interval SEC]

Options:
  --test-inverter       Test inverter connection only
  --test-generator      Test generator connection only
  --inverter-port PORT  Inverter serial port (default: /dev/ttySC1)
  --inverter-baud BAUD  Inverter baud rate (default: 19200)
  --generator-host HOST Generator server host (default: 10.2.242.109)
  --soc-start PCT       SOC threshold to start generator (default: 25)
  --soc-stop PCT        SOC threshold to stop generator (default: 80)
  --log-dir DIR         CSV log directory (default: /var/log/pigenny)
  --log-interval SEC    Seconds between CSV log entries (default: 600 = 10 min)
```

**Note**: The defaults for `--inverter-port` and `--inverter-baud` are configured for the Waveshare RS-485 HAT with LuxPower inverter. If running manually, you typically don't need to specify these parameters unless your hardware differs.

### Example Usage

```bash
# Test connections first
python3 monitor.py --test-generator
python3 monitor.py --test-inverter

# Run with defaults (start at 25%, stop at 80%)
python3 monitor.py

# Custom thresholds and logging
python3 monitor.py --soc-start 52 --soc-stop 57 --log-interval 300

# Override serial port if needed (not typically required)
python3 monitor.py --inverter-port /dev/ttyUSB0 --inverter-baud 9600
```

### CSV Data Logging

The monitor logs inverter and generator data to daily CSV files.

**Log Location**: `/var/log/pigenny/data_YYYYMMDD.csv`

**Log Interval**: Default 10 minutes (configurable via `--log-interval`)

**CSV Columns**:
```
timestamp, timestamp_unix, soc_pct, soh_pct, vbat_v, vpv1_v, vpv2_v,
pv_power_w, charge_power_w, discharge_power_w, generator_state, generator_running
```

**Logging Behavior**:
- New file created at midnight each day
- File is opened, written, and closed on each log entry (crash-safe)
- Header written only when creating a new file
- Poll interval (30s default) is separate from log interval (10 min default)

### Running in Background

**Recommended**: Use `tmux` to run the monitor in a persistent session:

```bash
# Start a new tmux session
tmux new -s pigenny

# Run the monitor
python3 monitor.py

# Detach from session: Ctrl+B, then D
# Reattach later: tmux attach -t pigenny
```

This keeps the monitor running even if your SSH session disconnects.

### Control Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| SOC Start | 25% | Start generator when SOC drops below |
| SOC Stop | 80% | Stop generator when SOC rises above |
| Poll Interval | 30s | Time between inverter reads |
| Log Interval | 600s | Time between CSV log entries |
| Generator Cooldown | 3600s | Min time between generator runs |
| Generator Max Runtime | 14400s (4 hours) | Maximum generator run time |

---

## SD Card Update Process

When you need to update the Olimex software:

### Step 1: Remove SD Card

1. Power off the Olimex
2. Remove the microSD card
3. Insert into Pi (or Mac/PC with SD reader)

### Step 2: Mount the SD Card

```bash
# Find the device
lsblk

# Create mount point and mount (adjust device as needed)
sudo mkdir -p /mnt/olimex
sudo mount /dev/sda2 /mnt/olimex

# Verify mount
ls /mnt/olimex/usr/local/bin/
```

**Note**: The root partition is typically `/dev/sda2` (or `/dev/mmcblk0p2` on some systems).

### Step 3: Run Install Script

```bash
cd ~/pigenny   # or wherever your pigenny directory is
sudo ./install_server.sh /mnt/olimex
```

The script will:
- Copy `gen_server.py` to `/usr/local/bin/`
- Create startup wrapper script
- Configure cron `@reboot` entry for auto-start
- **Disable the firewall** for SSH access
- Display configuration summary

### Step 4: Unmount

```bash
sudo umount /mnt/olimex
```

### Step 5: Boot and Test

1. Insert SD card back into Olimex
2. Connect Ethernet from Pi to Olimex (direct wire)
3. Set Pi's IP if not persistent:
   ```bash
   sudo ip addr add 10.2.242.1/24 dev eth0
   ```
4. Power on Olimex
5. Wait ~30 seconds for boot
6. Test:
   ```bash
   # Test SSH (should work after firewall disabled)
   ssh root@10.2.242.109

   # Test generator server
   python3 gen_client.py --host 10.2.242.109
   ```

### Quick Test Commands

In the gen_client interactive mode:
```
genny> ping
PONG

genny> status
INPUTS: IN1=0 IN2=0 IN3=0 IN4=0 raw=0
RELAYS: OFF (0x00)
RUNNING: NO
START_IN_PROGRESS: NO
I2C: OK
END

genny> quit
```

---

## Inverter Communication

### RS-485 Settings

| Parameter | Value |
|-----------|-------|
| Port | /dev/ttyUSB0 or /dev/ttySC1 |
| Baud Rate | 9600 (monitor.py) or 19200 (dump_ongoing.py) |
| Parity | None |
| Data Bits | 8 |
| Stop Bits | 1 |
| Slave ID | 1 |

### Key Registers (Input Registers - Function 0x04)

| Register | Description | Unit | Decoding |
|----------|-------------|------|----------|
| 1 | PV1 Voltage | 0.1V | value / 10 |
| 2 | PV2 Voltage | 0.1V | value / 10 |
| 4 | Battery Voltage | 0.1V | value / 10 |
| 5 | SOC/SOH Combined | % | SOC = value & 0xFF, SOH = value >> 8 |
| 9 | Total PV Power | W | direct |
| 10 | Battery Charge Power | W | direct |
| 11 | Battery Discharge Power | W | direct |

---

## Software Setup on Pi

### Package Installation

```bash
# System packages
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip tmux

# Python packages (Raspberry Pi OS - use apt)
sudo apt install -y python3-pymodbus python3-serial

# Or in a virtual environment
python3 -m venv ~/pigenny-venv
source ~/pigenny-venv/bin/activate
pip install pymodbus pyserial
```

### Create Log Directory

```bash
sudo mkdir -p /var/log/pigenny
sudo chown $USER:$USER /var/log/pigenny
```

### RS-485 HAT Configuration (if using Waveshare HAT)

For Waveshare HAT with SC16IS752 chip, add to `/boot/config.txt`:
```
dtoverlay=sc16is752-spi1,int_pin=24
```

Reboot and verify:
```bash
ls /dev/ttySC*
```

---

## Automatic Startup (systemd Service)

### Default Configuration

The Pi is configured to automatically run `monitor.py` on boot with these parameters:
- **Start generator**: SOC < 40%
- **Stop generator**: SOC >= 80%
- **Max runtime**: 4 hours (14400 seconds)
- **Log interval**: 10 minutes
- **Serial port**: `/dev/ttySC1` (Waveshare RS-485 HAT)
- **Baud rate**: 19200

### Initial Setup

**Status**: ✓ This service is already configured and running on the Pi (setup completed Jan 1, 2026).

To set up the service from scratch on a new system, follow these steps:

**1. Create the log directory:**
```bash
sudo mkdir -p /var/log/pigenny
sudo chown $USER:$USER /var/log/pigenny
```

**2. Create the systemd service file:**
```bash
sudo nano /etc/systemd/system/pigenny.service
```

**3. Paste this content:**

```ini
[Unit]
Description=PiGenny Generator Monitor
After=network.target
Wants=network.target

[Service]
Type=simple
User=derekja
WorkingDirectory=/home/derekja/pigenny
ExecStart=/usr/bin/python3 /home/derekja/pigenny/monitor.py --inverter-port /dev/ttySC1 --inverter-baud 19200 --soc-start 40 --soc-stop 80 --log-interval 600
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Note**: Change `User` and `WorkingDirectory` to match your username and pigenny location if different.

**4. Enable and start the service:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable pigenny
sudo systemctl start pigenny
```

**5. Verify it's running:**
```bash
sudo systemctl status pigenny
```

You should see output showing:
```
Active: active (running)
INFO | Connected to inverter on /dev/ttySC1
INFO | Connecting to generator server...
```

### Managing the Service

**View live logs:**
```bash
journalctl -u pigenny -f
```

**Check status:**
```bash
sudo systemctl status pigenny
```

**Stop the service:**
```bash
sudo systemctl stop pigenny
```

**Start the service:**
```bash
sudo systemctl start pigenny
```

**Restart the service:**
```bash
sudo systemctl restart pigenny
```

**Disable auto-start on boot (but keep running now):**
```bash
sudo systemctl disable pigenny
```

**Enable auto-start on boot:**
```bash
sudo systemctl enable pigenny
```

### Running Manually with Custom Parameters

If you want to stop the automatic service and run monitor.py manually with different parameters:

**1. Stop the automatic service:**
```bash
sudo systemctl stop pigenny
```

**2. Start a tmux session:**
```bash
tmux new -s pigenny
```

**3. Run monitor.py with your custom parameters:**
```bash
# Example: Start at 30%, stop at 90%, log every 5 minutes
python3 monitor.py --soc-start 30 --soc-stop 90 --log-interval 300

# The serial port and baud rate use correct defaults, no need to specify them
```

**4. Detach from tmux (to leave it running in background):**
```
Press: Ctrl+B, then D
```

**5. Reattach to the tmux session later:**
```bash
tmux attach -t pigenny
```

**Important Notes:**
- The manual process will continue running until you stop it or reboot
- On Pi reboot, the systemd service will automatically start again with the default parameters (40%/80%)
- If you want to permanently change the default parameters, see "Changing Default Parameters" below

### Changing Default Parameters

To permanently change the default SOC thresholds or other parameters:

**1. Edit the service file:**
```bash
sudo nano /etc/systemd/system/pigenny.service
```

**2. Modify the `ExecStart` line parameters:**
```ini
# Example: Change to start at 35%, stop at 85%, log every 5 minutes
ExecStart=/usr/bin/python3 /home/derekja/pigenny/monitor.py --inverter-port /dev/ttySC1 --inverter-baud 19200 --soc-start 35 --soc-stop 85 --log-interval 300
```

**3. Reload and restart:**
```bash
sudo systemctl daemon-reload
sudo systemctl restart pigenny
```

**4. Verify new parameters:**
```bash
journalctl -u pigenny -n 20 --no-pager
```

Look for the line showing your new thresholds:
```
INFO | SOC thresholds: start < 35%, stop >= 85%
```

### Checking What's Running

To see if monitor.py is running and how:

```bash
# Check if systemd service is running
sudo systemctl status pigenny

# Check for any monitor.py processes
ps aux | grep monitor.py

# See the command line arguments being used
ps aux | grep -v grep | grep monitor.py
```

**Warning**: Don't run both the systemd service AND a manual monitor.py at the same time - they will conflict trying to control the same generator.

---

## Maintenance Tools

### update_genserver.py - Automated Olimex Updates

This Python 2 script automates deployment of gen_server.py updates to the Olimex, eliminating manual SSH, copying, and restart steps.

**Location**: `/usr/local/bin/update_genserver.py` on Olimex

**Usage**:
```bash
# On Olimex (requires sudo/root)
sudo python2 /usr/local/bin/update_genserver.py [--source /path/to/gen_server.py]
```

**Default source**: `/home/derekja/gen_server.py`

**What it does**:
1. Verifies source file exists and checks size
2. Finds currently running gen_server.py process (if any)
3. Copies file to `/usr/local/bin/gen_server.py` with correct permissions (755)
4. Kills old process gracefully (SIGTERM, then SIGKILL if needed)
5. Waits for rc.local to auto-restart the server (up to 30 seconds)
6. Verifies new process started and is responding on port 9999

**Options**:
- `--source PATH` - Specify alternate source file path
- `--no-verify` - Skip verification checks (not recommended)

**Remote deployment from your computer**:
```bash
# From local machine through Pi to Olimex
scp -i ~/.ssh/momspi pigenny/gen_server.py derekja@momspi.local:/tmp/
ssh -i ~/.ssh/momspi derekja@momspi.local \
  "sshpass -p 'Login123' scp /tmp/gen_server.py derekja@10.2.242.109:/home/derekja/ && \
   sshpass -p 'Login123' ssh derekja@10.2.242.109 'echo Login123 | sudo -S python2 /usr/local/bin/update_genserver.py'"
```

**Warning**: This will immediately shut down the generator if it's running! Only deploy when:
- Generator is not running
- Generator has completed a proper stop sequence with cooldown
- You're willing to accept immediate generator shutdown

### genserverstatus.py - Health & Status Monitoring

Query generator status and Olimex system health metrics.

**Locations**:
- Olimex: `/usr/local/bin/genserverstatus.py` (local queries)
- Pi: `/home/derekja/pigenny/genserverstatus.py` (remote queries)

**Usage**:
```bash
# On Olimex (local query)
python2 /usr/local/bin/genserverstatus.py

# From Pi (remote query)
python2 /home/derekja/pigenny/genserverstatus.py --host 10.2.242.109

# Compact single-line format
python2 genserverstatus.py --format compact

# Key=value format for scripting
python2 genserverstatus.py --format kv
```

**Output includes**:
- **Generator State**: Running status, relay states (IGN, START, GLOW, CHARGER), input states
- **System Health**: Active threads, uptime, memory usage %, disk space available
- **I2C Status**: Whether MOD-IO board communication is working

**Output Formats**:

**human** (default) - Multi-line readable format:
```
============================================================
Generator Server Status - 10.2.242.109:9999
============================================================

GENERATOR:
  Running:        YES
  Start Progress: NO
  Relays:         IGN+CHARGER (0x0A)
  Inputs:         IN1=1 IN2=1 IN3=0 IN4=0 raw=3

SYSTEM HEALTH:
  Active Threads: 2
  Uptime:         6h45m
  Memory Used:    42%
  Disk Usage:     1.2G free (35% used)
  I2C Status:     OK
```

**compact** - Single line for dashboards:
```
[10.2.242.109:9999] RUN=YES RELAY=IGN+CHARGER THR=2 UP=6h45m MEM=42%
```

**kv** - Key=value pairs for parsing/scripting:
```
host=10.2.242.109
port=9999
running=YES
relays=IGN+CHARGER (0x0A)
threads=2
uptime=6h45m
memory=42%
disk=1.2G free (35% used)
i2c=OK
```

### Automated Olimex Health Monitoring

The Pi's monitor.py automatically monitors Olimex system health and logs metrics to the Pi's system logs.

**Health check interval**: 1 hour (3600 seconds, configurable)

**Metrics monitored**:
- **Active threads** - Detects thread leaks (normal: 0-3 threads)
- **Uptime** - Detects unexpected reboots
- **Memory usage** - Detects memory leaks (normal: <60%)
- **Disk space** - Prevents log file exhaustion (warn if <500MB free)

**View health logs**:
```bash
# On Pi - view recent health checks
journalctl -u pigenny | grep "Olimex health"

# Example output:
# 2026-01-02 15:30:00 | INFO | Olimex health: threads=2 uptime=6h45m memory=42% disk=1.2G free (35% used)
```

**Normal values**:
- Threads: 0-3 (0 = idle, 1-3 = active connections)
- Memory: 30-60% (Olimex has 64MB RAM)
- Disk: Should have >500MB free
- Uptime: Should match expected runtime

**Warning signs**:
- Threads >10: Possible thread leak, check for "can't start new thread" errors
- Memory >80%: Memory leak or excessive logging
- Disk <100MB free: Log rotation needed
- Uptime resets unexpectedly: Investigate cause of reboot

---

## Troubleshooting

### Can't connect to Olimex

1. Check IP address is set on Pi: `ip addr show eth0`
2. Ping the Olimex: `ping 10.2.242.109`
3. Check if server is running: `nc -zv 10.2.242.109 9999`
4. SSH to Olimex and check logs: `cat /var/log/gen_server.log`

### SSH connection refused

Run the install script again to disable the firewall, then reboot the Olimex.

### Generator won't start

1. Check STATUS - verify I2C is "OK" not "SIMULATED"
2. Check INPUTS - verify input sensing is working
3. Try manual relay control: `RELAY 08` (IGN only)
4. Check physical connections

### Inverter communication errors

1. Verify serial port: `ls /dev/ttyUSB* /dev/ttySC*`
2. Check baud rate matches inverter settings
3. Test with: `python3 monitor.py --test-inverter`

### CSV logs not appearing

1. Check log directory exists: `ls -la /var/log/pigenny/`
2. Check permissions: directory should be writable
3. Verify log interval isn't too long

---

## Safety Considerations

### Generator Protection

- Maximum crank time: 12 seconds (prevents starter damage)
- Glow plugs: 2 seconds max (prevents element damage)
- 3-minute cooldown on stop (protects turbo/bearings)
- Cooldown period between start attempts

### Charger Protection

- 60-second warmup after generator starts
- 20-second AC stabilization delay before charger enable
- External 10-second timer relay for additional protection

### Power Failure

- CSV logging with open/write/close pattern survives crashes
- Consider UPS for Pi if frequent power outages
- Olimex has very low power consumption

---

## Quick Reference

### IP Addresses
- Olimex: `10.2.242.109`
- Pi: `10.2.242.1`

### Ports
- Generator server: TCP `9999`

### Default Settings
- SOC Start: 40%
- SOC Stop: 80%
- Log Interval: 10 minutes (600s)
- Serial Port: `/dev/ttySC1`
- Baud Rate: 19200

### Key Commands
```bash
# Set Pi IP (temporary)
sudo ip addr add 10.2.242.1/24 dev eth0

# Test generator connection
python3 gen_client.py --host 10.2.242.109

# Check monitor service status
sudo systemctl status pigenny
journalctl -u pigenny -f

# Stop automatic monitor and run manually with custom thresholds
sudo systemctl stop pigenny
tmux new -s pigenny
python3 monitor.py --soc-start 52 --soc-stop 57
# Ctrl+B, D to detach

# Restart automatic monitor with defaults (40%/80%)
sudo systemctl start pigenny

# Update Olimex SD card
sudo mount /dev/sda2 /mnt/olimex
sudo ./install_server.sh /mnt/olimex
sudo umount /mnt/olimex
```

### Generator Commands
```
PING     - Test connection
STATUS   - Full status report
START    - Start generator (blocking ~2 min)
STOP     - Stop with 3 min cooldown
RELAY xx - Direct relay control (hex)
```
