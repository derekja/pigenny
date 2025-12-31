# PiGenny - Raspberry Pi Generator Controller

Generator autostart system for solar-powered battery backup using a Raspberry Pi 4B.

## Overview

This system monitors battery state of charge via RS-485 Modbus communication with a LuxPower/GSL inverter and automatically starts a backup generator when battery levels drop below a threshold.

---

## Hardware

### Components

| Component | Model | Purpose |
|-----------|-------|---------|
| Single Board Computer | Raspberry Pi 4B (2GB+ RAM) | Main controller |
| RS-485 Interface | Waveshare RS485 HAT | Inverter communication |
| Relay Board | Elegoo 4-channel 5V DC relay | Generator control signals |
| Optoisolator (TBD) | 4-channel optocoupler module | Generator status inputs |
| Storage | 32GB+ microSD | OS and data |
| Power Supply | 5V 3A USB-C | Pi power |

**Note**: The optoisolator is needed to safely interface generator status signals (potentially 12V or higher) with Pi GPIO (3.3V). The existing Olimex MOD-IO has built-in optoisolated inputs.

### Relay Assignments

| Relay | Bit | Function | Destination | Notes |
|-------|-----|----------|-------------|-------|
| REL1 | 0 (0b0001) | Starter Motor | Generator | Cranking relay |
| REL2 | 1 (0b0010) | Charger Power Enable | 45EG20AG coil (24VAC) | Enables charger after AC stable |
| REL3 | 2 (0b0100) | Glow Plugs | Generator | Diesel preheat (2s during crank) |
| REL4 | 3 (0b1000) | Ignition/Run/Fuel | Generator | Main run enable, fuel solenoid |

### GPIO Pin Mapping

| Relay | BCM GPIO | Physical Pin | Function |
|-------|----------|--------------|----------|
| REL1 | GPIO17 | 11 | Starter Motor |
| REL2 | GPIO27 | 13 | Charger Power Enable |
| REL3 | GPIO22 | 15 | Glow Plugs |
| REL4 | GPIO23 | 16 | Ignition/Run/Fuel |

**Note**: Elegoo relay board is **active LOW** - GPIO LOW = relay energized.

### Wiring Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Raspberry Pi 4B                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    GPIO Header                             │  │
│  │                                                            │  │
│  │  RELAY OUTPUTS (Active LOW):                               │  │
│  │  Pin 2 (5V) ──────────────────────► Relay Board VCC       │  │
│  │  Pin 6 (GND) ─────────────────────► Relay Board GND       │  │
│  │  Pin 11 (GPIO17) ─────────────────► Relay 1 (STARTER)     │  │
│  │  Pin 13 (GPIO27) ─────────────────► Relay 2 (CHARGER EN)  │  │
│  │  Pin 15 (GPIO22) ─────────────────► Relay 3 (GLOW)        │  │
│  │  Pin 16 (GPIO23) ─────────────────► Relay 4 (IGN/RUN)     │  │
│  │                                                            │  │
│  │  GENERATOR STATUS INPUTS (accent Optoisolator):              │  │
│  │  Pin 18 (GPIO24) ◄── [OPTO] ◄──── IN1 Engine Running      │  │
│  │  Pin 22 (GPIO25) ◄── [OPTO] ◄──── IN2 Ignition Active     │  │
│  │  Pin 26 (GPIO7)  ◄── [OPTO] ◄──── IN4 Starter Engaged     │  │
│  │                                   (IN3 unused - not connected)│  │
│  │                                                            │  │
│  │  AC CONFIRMATION (future):                                 │  │
│  │  Pin 29 (GPIO5)  ◄──────────────── Supco Relay (240VAC)   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Waveshare RS-485 HAT (Stacked)                │  │
│  │  A/+ ─────────────────────────────► Inverter RS-485 A     │  │
│  │  B/- ─────────────────────────────► Inverter RS-485 B     │  │
│  │  GND ─────────────────────────────► Inverter RS-485 GND   │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Important**: Generator status inputs MUST go through an optoisolator module (e.g., PC817-based 4-channel board) to protect the Pi's 3.3V GPIO from the generator's signal voltage.

---

## AC Power Path and Charger Control

### Charger Power Enable Circuit

REL2 controls a **45EG20AG coil** driven by a **24VAC signal**. When REL2 is energized:
1. 24VAC is applied to the 45EG20AG coil
2. The contactor closes, connecting generator AC to the battery charger
3. Charger begins operation

### AC Stabilization

The generator AC output passes through an **external timer relay** that provides approximately **10 seconds of stabilization delay** before power reaches the charger circuit. This ensures:
- Generator reaches stable operating speed
- AC voltage and frequency stabilize
- Prevents charger damage from unstable power

The software may include an additional short delay as a safety margin.

### AC Confirmation (Future Enhancement)

A **Supco relay** will be installed to detect the presence of stable 240VAC:
- Input: 240VAC from generator (after timer relay)
- Output: Dry contact closure to Pi GPIO input
- Purpose: Positive confirmation that AC power is present and stable

This will allow the software to:
- Verify generator is actually producing power
- Detect generator failures during operation
- Implement more sophisticated control logic

---

## Generator Status Input Sensing

### Current Implementation (Olimex MOD-IO)

The existing system uses an Olimex MOD-IO board with **4 optoisolated inputs** (IN1-IN4) connected to generator status signals. Register 0x20 returns the input state as a bitmask.

### Generator Signal Connections (Confirmed)

Diagnostic test performed 2024-12-30 confirmed the following input mappings:

| Input | Wire Color | Function | Behavior |
|-------|------------|----------|----------|
| IN1 | Blue-black | **Engine Running** | Goes HIGH when engine catches, stays HIGH while running |
| IN2 | Blue | **Ignition Circuit Active** | Goes HIGH immediately when IGN relay energized |
| IN3 | Blue-white | Unused | Never activated during normal operation |
| IN4 | Green-black | **Starter Motor Engaged** | HIGH only while starter is cranking |

All inputs share generator black (ground) on the common terminal.

### Diagnostic Test Timeline

```
Time        Relay State     Status  Inputs          Event
────────────────────────────────────────────────────────────────
14:48:00    OFF             0       none            Baseline
14:48:10    IGN             2       IN2             Fuel solenoid energized
14:48:15    IGN+START       2       IN2             Cranking begins
14:48:20    IGN+START       10      IN2+IN4         Starter signal detected
14:48:26    IGN+GLOW+START  11      IN1+IN2+IN4     ENGINE CATCHES
14:48:28    IGN             3       IN1+IN2         Running, starter released
14:48:33    IGN             3       IN1+IN2         Confirmed running
14:49:03    IGN+CHARGER     3       IN1+IN2         Charger enabled
```

### Status Interpretation

| Status Value | Binary | Active Inputs | Generator State |
|--------------|--------|---------------|-----------------|
| 0 | 0b0000 | None | Off/Idle |
| 2 | 0b0010 | IN2 | Ignition on, engine not running |
| 3 | 0b0011 | IN1 + IN2 | **Running** |
| 10 | 0b1010 | IN2 + IN4 | Cranking (starter engaged) |
| 11 | 0b1011 | IN1 + IN2 + IN4 | Engine catching (starter still engaged) |

The code checks for `status == 3` to confirm the generator has started successfully (IN1 engine running + IN2 ignition active).

### Signal Sources

Based on diagnostic results:
- **IN1**: Oil pressure switch or tachometer signal - indicates engine is actually running
- **IN2**: Ignition/fuel solenoid circuit feedback - confirms relay activated the circuit
- **IN3**: Not connected or fault indicator (never activated)
- **IN4**: Starter motor engagement feedback - useful for detecting crank issues

### Pi Implementation Requirements

To replicate this functionality on the Raspberry Pi, GPIO inputs are needed for the generator status signals:

| Pi GPIO | Physical Pin | Generator Signal | Required |
|---------|--------------|------------------|----------|
| GPIO24 | 18 | IN1 - Engine Running | **Yes** |
| GPIO25 | 22 | IN2 - Ignition Active | **Yes** |
| GPIO7 | 26 | IN4 - Starter Engaged | Optional (useful for diagnostics) |

**Note**: IN3 is unused and does not need to be connected.

**Important**: The generator signals require **optoisolation** to safely interface with Pi GPIO (3.3V). The existing MOD-IO board has built-in optoisolated inputs. For the Pi, use a 4-channel optocoupler module (e.g., PC817-based board) between the generator signals and the Pi GPIO.

### Status Detection Logic

```python
import RPi.GPIO as GPIO

# Input pin configuration (accent optoisolator module between generator and Pi)
INPUT_PINS = {
    'engine_running': 24,   # BCM GPIO24 - IN1: Oil pressure/tach (HIGH = running)
    'ignition_active': 25,  # BCM GPIO25 - IN2: Ignition circuit (HIGH = energized)
    'starter_engaged': 7,   # BCM GPIO7  - IN4: Starter motor (HIGH = cranking)
}

def setup_inputs():
    for pin in INPUT_PINS.values():
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

def is_generator_running():
    """
    Returns True if generator is confirmed running.
    Requires both engine_running (IN1) AND ignition_active (IN2) to be HIGH.
    This matches the original Olimex logic: status == 3
    """
    engine = GPIO.input(INPUT_PINS['engine_running'])
    ignition = GPIO.input(INPUT_PINS['ignition_active'])
    return engine == GPIO.HIGH and ignition == GPIO.HIGH

def get_status_byte():
    """Returns status byte compatible with original Olimex implementation"""
    status = 0
    if GPIO.input(INPUT_PINS['engine_running']):
        status |= 0b0001  # IN1
    if GPIO.input(INPUT_PINS['ignition_active']):
        status |= 0b0010  # IN2
    if GPIO.input(INPUT_PINS['starter_engaged']):
        status |= 0b1000  # IN4
    return status

def get_generator_state():
    """Returns human-readable generator state"""
    status = get_status_byte()
    if status == 0:
        return "OFF"
    elif status == 2:
        return "IGNITION_ON"  # Fuel solenoid energized, not running
    elif status == 3:
        return "RUNNING"      # Engine running normally
    elif status == 10:
        return "CRANKING"     # Starter engaged, not caught yet
    elif status == 11:
        return "STARTING"     # Engine catching, starter still engaged
    else:
        return "UNKNOWN_%d" % status
```

### Signal Identification (Completed)

Diagnostic test completed 2024-12-30 using `diagnose_inputs.py` on the Olimex system. The test:

1. Logged input states every 100ms during a full start sequence
2. Correlated state transitions with generator behavior
3. Documented exact timing of each signal change
4. Confirmed wire color to function mapping

Results are documented in the "Generator Signal Connections (Confirmed)" section above.

---

## Generator Start Sequence

### Timing Diagram

```
Time(s)  Relays      Binary   Action
─────────────────────────────────────────────────────────────
  0.0    All OFF     0b0000   RESET: All relays off
  1.0    IGN         0b1000   IGNITION ON (fuel solenoid energized)

  1.0+   IGN+START   0b1001   IGNITION + CRANK START
 11.0                         (10 second crank attempt)

 11.0    IGN+GLOW    0b1101   IGNITION + GLOW PLUGS + CRANK
 13.0    +START               (2 second glow-assisted crank)

 13.0    IGN         0b1000   IGNITION ONLY (crank released)
 17.0                         (4 second coast/check)

 17.0    Check if running
         ├─ IF running:
         │   └─ Sleep 60s warmup period
         │       └─ IGN+CHARGER  0b1010   Enable charger power
         │           Return SUCCESS
         │
         └─ ELSE: Start failed
             └─ All OFF  0b0000   Emergency shutdown
                 Return FAILURE
```

### Phase Details

| Phase | Duration | Relays | Purpose |
|-------|----------|--------|---------|
| Reset | 1s | All OFF | Clean state |
| Fuel Enable | 0.5s | IGN | Energize fuel solenoid |
| Initial Crank | 10s | IGN + START | First crank attempt |
| Glow Crank | 2s | IGN + GLOW + START | Glow-assisted cranking |
| Coast/Check | 4s | IGN | Release starter, verify running |
| Warmup | 60s | IGN | Let engine reach operating temp |
| Running | Continuous | IGN + CHARGER | Enable charger, maintain run |

### Shutdown

All relays OFF immediately:
- Fuel solenoid closes (engine starves)
- Charger disconnected
- Engine coasts to stop

---

## Inverter Communication

### RS-485 Settings

| Parameter | Value |
|-----------|-------|
| Port | /dev/ttySC1 (or /dev/ttyS0) |
| Baud Rate | 19200 |
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
| 7 | PV1 Power | W | direct |
| 8 | PV2 Power | W | direct |
| 9 | Total PV Power | W | direct |
| 10 | Battery Charge Power | W | direct |
| 11 | Battery Discharge Power | W | direct |
| 64 | Internal Temperature | °C | direct |
| 101 | Max Cell Voltage | 0.001V | value / 1000 |
| 102 | Min Cell Voltage | 0.001V | value / 1000 |
| 103 | Max Cell Temperature | 0.1°C | value / 10 |
| 104 | Min Cell Temperature | 0.1°C | value / 10 |

### SOC/SOH Decoding

Register 5 contains both values packed:
```python
def decode_soc_soh(register_5_value):
    soc = register_5_value & 0xFF        # Low byte = State of Charge %
    soh = register_5_value >> 8          # High byte = State of Health %
    return soc, soh
```

---

## Control Logic

### Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| SOC_LOW | 25% | Start generator below this level |
| SOC_HIGH | 80% | Stop generator above this level |
| POLL_INTERVAL | 30s | Time between inverter reads |
| GENERATOR_COOLDOWN | 3600s | Minimum time between generator runs |
| GENERATOR_RUNTIME | 7200s | Maximum generator run time |

### State Machine

```
                    ┌─────────────┐
                    │   IDLE      │
                    │ (Monitoring)│
                    └──────┬──────┘
                           │
                    SOC < LOW?
                           │
              YES ─────────┴───────── NO
               │                      │
               ▼                      │
        ┌─────────────┐               │
        │  STARTING   │               │
        │ (Crank seq) │               │
        └──────┬──────┘               │
               │                      │
          Success?                    │
               │                      │
      YES ─────┴───── NO              │
       │              │               │
       ▼              ▼               │
┌─────────────┐  ┌─────────┐         │
│  RUNNING    │  │ FAILED  │         │
│ (Charging)  │  │ (Retry?)│         │
└──────┬──────┘  └─────────┘         │
       │                              │
  SOC >= HIGH?                        │
       │                              │
  YES ─┴─ NO                          │
   │      │                           │
   │      └───────────────────────────┤
   │                                  │
   ▼                                  │
┌─────────────┐                       │
│  STOPPING   │                       │
│ (Shutdown)  │───────────────────────┘
└─────────────┘
```

---

## Software Setup

### OS Installation

1. Flash "Raspberry Pi OS Lite (64-bit)" to SD card
2. Enable SSH during imaging
3. Set hostname: `pigenny`
4. Configure network

### Package Installation

```bash
# System packages
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv \
    python3-gpiozero python3-serial git

# Python packages
pip3 install pyserial RPi.GPIO gpiozero

# Enable serial port for RS-485 HAT
sudo raspi-config
# Interface Options → Serial Port
# Login shell over serial: NO
# Serial hardware enabled: YES
```

### RS-485 HAT Configuration

For Waveshare HAT with SC16IS752 chip, add to `/boot/config.txt`:
```
dtoverlay=sc16is752-spi1,int_pin=24
```

Reboot and verify:
```bash
ls /dev/ttySC*
```

---

## File Structure

```
pigenny/
├── documentation.md        # This file
├── generator_control.py    # GPIO relay control module
├── inverter_monitor.py     # RS-485 inverter communication
├── main.py                 # Main daemon combining both
├── config.yaml             # Configuration file
└── tests/
    ├── test_relays.py      # Relay board test
    └── test_inverter.py    # RS-485 communication test
```

---

## systemd Service

Create `/etc/systemd/system/pigenny.service`:

```ini
[Unit]
Description=PiGenny Generator Controller
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/pigenny
ExecStart=/usr/bin/python3 /home/pi/pigenny/main.py
Restart=always
RestartSec=10
ExecStop=/usr/bin/python3 /home/pi/pigenny/generator_control.py stop
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable pigenny
sudo systemctl start pigenny
```

---

## Safety Considerations

### Power Failure

- Implement UPS or graceful shutdown on power loss
- Consider read-only root filesystem for SD card reliability

### Generator Protection

- Maximum crank time: 12 seconds (prevents starter damage)
- Cooldown period between start attempts
- Automatic shutdown on failure

### Charger Protection

- 10-second timer relay delay for AC stabilization
- Software delay as additional safety margin
- Future: AC confirmation via Supco relay before enabling charger

---

## Future Enhancements

1. **AC Confirmation Input**
   - Supco relay providing GPIO input when 240VAC present
   - Positive confirmation before enabling charger
   - Generator failure detection during operation

2. **Web Interface**
   - Real-time status display
   - Manual control overrides
   - Historical data graphs

3. **Notifications**
   - Email/SMS alerts on generator start/stop
   - Low battery warnings
   - Failure notifications

4. **Data Logging**
   - CSV logging of inverter data
   - Generator run history
   - Battery cycle tracking
