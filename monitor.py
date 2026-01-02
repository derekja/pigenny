#!/usr/bin/env python3
"""
PiGenny Monitor - Inverter monitoring with automatic generator control

Reads battery SOC from LuxPower inverter via RS-485 and controls
generator via TCP connection to Olimex.

Usage:
  python3 monitor.py [--config config.yaml]
  python3 monitor.py --test-inverter    # Test inverter connection only
  python3 monitor.py --test-generator   # Test generator connection only
"""

import time
import sys
import argparse
import logging
import os
import csv
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# Try to import pymodbus for inverter communication
try:
    from pymodbus.client import ModbusSerialClient
    MODBUS_AVAILABLE = True
except ImportError:
    try:
        # Older pymodbus version
        from pymodbus.client.sync import ModbusSerialClient
        MODBUS_AVAILABLE = True
    except ImportError:
        MODBUS_AVAILABLE = False
        log.warning("pymodbus not available - inverter communication disabled")

# Import our generator client
from gen_client import GeneratorClient


# =============================================================================
# Configuration
# =============================================================================

CONFIG = {
    # Inverter RS-485 settings
    'inverter_port': '/dev/ttySC1',   # Waveshare RS-485 HAT
    'inverter_baud': 19200,           # LuxPower inverter baud rate
    'inverter_slave_id': 1,

    # Generator server settings
    'generator_host': '10.2.242.109',
    'generator_port': 9999,

    # Control thresholds
    'soc_start_threshold': 25,    # Start generator when SOC drops below this
    'soc_stop_threshold': 80,     # Stop generator when SOC rises above this

    # Timing
    'poll_interval': 30,          # Seconds between inverter reads
    'generator_cooldown': 3600,   # Minimum seconds between generator runs
    'generator_max_runtime': 14400, # Maximum seconds for generator to run (4 hours)

    # Safety
    'max_start_attempts': 3,      # Max consecutive start failures before giving up

    # Logging
    'csv_log_dir': '/var/log/pigenny',
    'csv_log_prefix': 'data_',
    'log_interval': 600,          # Seconds between CSV log entries (default 10 min)
    'olimex_health_check_interval': 3600,  # Seconds between Olimex health checks (default 1 hour)
}


# =============================================================================
# CSV Logging
# =============================================================================

class CSVLogger:
    """Logs data to daily CSV files - opens, writes, closes on each entry"""

    CSV_FIELDS = [
        'timestamp', 'timestamp_unix', 'soc_pct', 'soh_pct', 'vbat_v',
        'vpv1_v', 'vpv2_v', 'pv_power_w', 'charge_power_w', 'discharge_power_w',
        'generator_state', 'generator_running'
    ]

    def __init__(self, log_dir, prefix='data_'):
        self.log_dir = log_dir
        self.prefix = prefix
        self._ensure_dir()

    def _ensure_dir(self):
        """Ensure log directory exists"""
        if not os.path.exists(self.log_dir):
            try:
                os.makedirs(self.log_dir)
                log.info(f"Created log directory: {self.log_dir}")
            except Exception as e:
                log.error(f"Failed to create log directory {self.log_dir}: {e}")

    def _get_log_path(self, dt):
        """Get log file path for a given date (new file each day at midnight)"""
        date_str = dt.strftime('%Y%m%d')
        return os.path.join(self.log_dir, f"{self.prefix}{date_str}.csv")

    def log_data(self, inverter_data, generator_state, generator_running):
        """Log a data row to CSV - opens, writes, and closes file each time"""
        now = datetime.now()
        log_path = self._get_log_path(now)

        # Check if file exists to determine if we need header
        file_exists = os.path.exists(log_path)

        row = {
            'timestamp': now.strftime('%Y-%m-%dT%H:%M:%S'),
            'timestamp_unix': int(now.timestamp()),
            'soc_pct': inverter_data.get('soc', ''),
            'soh_pct': inverter_data.get('soh', ''),
            'vbat_v': inverter_data.get('battery_voltage', ''),
            'vpv1_v': inverter_data.get('pv1_voltage', ''),
            'vpv2_v': inverter_data.get('pv2_voltage', ''),
            'pv_power_w': inverter_data.get('pv_power', ''),
            'charge_power_w': inverter_data.get('charge_power', ''),
            'discharge_power_w': inverter_data.get('discharge_power', ''),
            'generator_state': generator_state,
            'generator_running': 1 if generator_running else 0,
        }

        try:
            # Open, write header if needed, write row, close
            with open(log_path, 'a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.CSV_FIELDS)
                if not file_exists:
                    writer.writeheader()
                    log.info(f"Created new log file: {log_path}")
                writer.writerow(row)
            return True
        except Exception as e:
            log.error(f"Failed to write CSV row to {log_path}: {e}")
            return False

    def close(self):
        """No-op since we open/close on each write"""
        pass


# =============================================================================
# Inverter Communication
# =============================================================================

class InverterMonitor:
    """Reads data from LuxPower inverter via RS-485 Modbus"""

    def __init__(self, port, baudrate=9600, slave_id=1):
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.client = None

    def connect(self):
        """Connect to inverter"""
        if not MODBUS_AVAILABLE:
            log.error("pymodbus not installed")
            return False

        try:
            self.client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                parity='N',
                stopbits=1,
                bytesize=8,
                timeout=3
            )
            if self.client.connect():
                log.info(f"Connected to inverter on {self.port}")
                return True
            else:
                log.error(f"Failed to connect to inverter on {self.port}")
                return False
        except Exception as e:
            log.error(f"Inverter connection error: {e}")
            return False

    def disconnect(self):
        """Disconnect from inverter"""
        if self.client:
            self.client.close()

    def read_soc(self):
        """Read State of Charge from inverter. Returns SOC% or None on error."""
        if not self.client:
            return None

        try:
            # Register 5 contains SOC in low byte (based on earlier analysis)
            result = self.client.read_input_registers(5, count=1, slave=self.slave_id)
            if result.isError():
                log.error(f"Modbus read error: {result}")
                return None

            raw_value = result.registers[0]
            soc = raw_value & 0xFF  # Low byte is SOC
            return soc

        except Exception as e:
            log.error(f"Error reading SOC: {e}")
            return None

    def read_battery_voltage(self):
        """Read battery voltage. Returns voltage or None on error."""
        if not self.client:
            return None

        try:
            # Register 4 contains battery voltage (value / 10)
            result = self.client.read_input_registers(4, count=1, slave=self.slave_id)
            if result.isError():
                return None

            raw_value = result.registers[0]
            voltage = raw_value / 10.0
            return voltage

        except Exception as e:
            log.error(f"Error reading voltage: {e}")
            return None

    def read_all(self):
        """Read all relevant values. Returns dict or None on error."""
        if not self.client:
            return None

        try:
            # Read registers 0-20 in one batch
            result = self.client.read_input_registers(0, count=20, slave=self.slave_id)
            if result.isError():
                log.error(f"Modbus read error: {result}")
                return None

            regs = result.registers

            data = {
                'soc': regs[5] & 0xFF,
                'soh': regs[5] >> 8,
                'battery_voltage': regs[4] / 10.0,
                'pv1_voltage': regs[1] / 10.0,
                'pv2_voltage': regs[2] / 10.0,
                'pv_power': regs[9],
                'charge_power': regs[10],
                'discharge_power': regs[11],
            }
            return data

        except Exception as e:
            log.error(f"Error reading inverter data: {e}")
            return None


# =============================================================================
# Main Monitor
# =============================================================================

class PiGennyMonitor:
    """Main monitoring and control loop"""

    # States
    STATE_IDLE = 'IDLE'
    STATE_STARTING = 'STARTING'
    STATE_RUNNING = 'RUNNING'
    STATE_STOPPING = 'STOPPING'
    STATE_COOLDOWN = 'COOLDOWN'
    STATE_ERROR = 'ERROR'

    def __init__(self, config):
        self.config = config
        self.state = self.STATE_IDLE

        # Inverter
        self.inverter = InverterMonitor(
            config['inverter_port'],
            config['inverter_baud'],
            config['inverter_slave_id']
        )

        # Generator client
        self.generator = GeneratorClient(
            config['generator_host'],
            config['generator_port']
        )

        # CSV logging
        self.csv_logger = CSVLogger(
            config['csv_log_dir'],
            config['csv_log_prefix']
        )
        self.log_interval = config['log_interval']
        self.last_log_time = None

        # Olimex health monitoring
        self.olimex_health_check_interval = config['olimex_health_check_interval']
        self.last_health_check_time = None

        # Timing
        self.generator_started_at = None
        self.generator_stopped_at = None
        self.start_attempts = 0

        # Last readings
        self.last_soc = None
        self.last_voltage = None

    def connect(self):
        """Connect to inverter and generator"""
        log.info("Connecting to inverter...")
        if not self.inverter.connect():
            log.error("Failed to connect to inverter")
            return False

        log.info("Connecting to generator server...")
        try:
            self.generator.connect()
            log.info("Connected to generator server")
        except Exception as e:
            log.error(f"Failed to connect to generator: {e}")
            return False

        return True

    def disconnect(self):
        """Disconnect from all"""
        self.inverter.disconnect()
        self.generator.disconnect()
        self.csv_logger.close()

    def get_generator_status(self):
        """Get current generator status"""
        try:
            return self.generator.status()
        except:
            return None

    def is_generator_running(self):
        """Check if generator is currently running"""
        try:
            return self.generator.is_running()
        except:
            return False

    def start_generator(self):
        """Start the generator"""
        log.info("Starting generator...")
        self.state = self.STATE_STARTING

        try:
            response = self.generator.start()
            log.info(f"Start response: {response}")

            if "OK" in response:
                self.state = self.STATE_RUNNING
                self.generator_started_at = datetime.now()
                self.start_attempts = 0
                log.info("Generator started successfully")
                return True
            else:
                self.start_attempts += 1
                log.error(f"Generator start failed: {response}")
                self.state = self.STATE_ERROR if self.start_attempts >= self.config['max_start_attempts'] else self.STATE_IDLE
                return False

        except Exception as e:
            self.start_attempts += 1
            log.error(f"Generator start exception: {e}")
            self.state = self.STATE_ERROR if self.start_attempts >= self.config['max_start_attempts'] else self.STATE_IDLE
            return False

    def stop_generator(self):
        """Stop the generator"""
        log.info("Stopping generator...")
        self.state = self.STATE_STOPPING

        try:
            response = self.generator.stop()
            log.info(f"Stop response: {response}")

            self.state = self.STATE_COOLDOWN
            self.generator_stopped_at = datetime.now()
            self.generator_started_at = None
            return True

        except Exception as e:
            log.error(f"Generator stop exception: {e}")
            return False

    def check_cooldown(self):
        """Check if cooldown period has passed"""
        if self.generator_stopped_at is None:
            return True

        elapsed = (datetime.now() - self.generator_stopped_at).total_seconds()
        if elapsed >= self.config['generator_cooldown']:
            self.state = self.STATE_IDLE
            return True
        return False

    def check_max_runtime(self):
        """Check if generator has exceeded max runtime"""
        if self.generator_started_at is None:
            return False

        elapsed = (datetime.now() - self.generator_started_at).total_seconds()
        return elapsed >= self.config['generator_max_runtime']

    def check_olimex_health(self):
        """Check Olimex system health and log metrics"""
        try:
            status_text = self.generator.get_status()
            if not status_text:
                log.warning("Failed to get Olimex health status")
                return

            # Parse status response
            metrics = {}
            for line in status_text.split('\n'):
                line = line.strip()
                if ':' in line and line != 'END':
                    key, value = line.split(':', 1)
                    metrics[key.strip()] = value.strip()

            # Extract key health metrics
            threads = metrics.get('THREADS', 'unknown')
            uptime = metrics.get('UPTIME', 'unknown')
            memory = metrics.get('MEMORY', 'unknown')
            disk = metrics.get('DISK', 'unknown')

            log.info(f"Olimex health: threads={threads} uptime={uptime} memory={memory} disk={disk}")

        except Exception as e:
            log.warning(f"Failed to check Olimex health: {e}")

    def run_once(self):
        """Run one monitoring cycle"""
        # Read inverter
        data = self.inverter.read_all()
        if data:
            self.last_soc = data['soc']
            self.last_voltage = data['battery_voltage']
            log.info(f"SOC: {data['soc']}% | Voltage: {data['battery_voltage']}V | "
                    f"PV: {data['pv_power']}W | Charge: {data['charge_power']}W | "
                    f"Discharge: {data['discharge_power']}W")

            # Log to CSV at specified interval
            now = datetime.now()
            should_log = False
            if self.last_log_time is None:
                should_log = True  # First log
            else:
                elapsed = (now - self.last_log_time).total_seconds()
                if elapsed >= self.log_interval:
                    should_log = True

            if should_log:
                generator_running = self.is_generator_running()
                self.csv_logger.log_data(data, self.state, generator_running)
                self.last_log_time = now
                log.info(f"CSV logged (next in {self.log_interval}s)")

            # Check Olimex health at specified interval
            should_health_check = False
            if self.last_health_check_time is None:
                should_health_check = True  # First check
            else:
                health_elapsed = (now - self.last_health_check_time).total_seconds()
                if health_elapsed >= self.olimex_health_check_interval:
                    should_health_check = True

            if should_health_check:
                self.check_olimex_health()
                self.last_health_check_time = now
        else:
            log.warning("Failed to read inverter data")
            return

        soc = data['soc']

        # State machine
        if self.state == self.STATE_IDLE:
            # Check if we need to start
            if soc < self.config['soc_start_threshold']:
                log.info(f"SOC {soc}% below threshold {self.config['soc_start_threshold']}% - starting generator")
                self.start_generator()

        elif self.state == self.STATE_RUNNING:
            # Check if we should stop
            if soc >= self.config['soc_stop_threshold']:
                log.info(f"SOC {soc}% reached threshold {self.config['soc_stop_threshold']}% - stopping generator")
                self.stop_generator()
            elif self.check_max_runtime():
                log.warning("Generator max runtime exceeded - stopping")
                self.stop_generator()

        elif self.state == self.STATE_COOLDOWN:
            if self.check_cooldown():
                log.info("Cooldown complete, returning to idle")

        elif self.state == self.STATE_ERROR:
            log.error(f"In error state after {self.start_attempts} failed start attempts")
            # Could implement recovery logic here

        log.info(f"State: {self.state}")

    def run(self):
        """Main monitoring loop"""
        log.info("Starting PiGenny monitor...")
        log.info(f"SOC thresholds: start < {self.config['soc_start_threshold']}%, "
                f"stop >= {self.config['soc_stop_threshold']}%")
        log.info(f"Poll interval: {self.config['poll_interval']}s, "
                f"CSV log interval: {self.log_interval}s ({self.log_interval/60:.0f} min)")
        log.info(f"CSV log directory: {self.config['csv_log_dir']}")

        if not self.connect():
            return 1

        try:
            while True:
                self.run_once()
                time.sleep(self.config['poll_interval'])

        except KeyboardInterrupt:
            log.info("Interrupted by user")
        finally:
            self.disconnect()

        return 0


# =============================================================================
# Test Functions
# =============================================================================

def test_inverter(config):
    """Test inverter connection"""
    log.info("Testing inverter connection...")

    monitor = InverterMonitor(
        config['inverter_port'],
        config['inverter_baud'],
        config['inverter_slave_id']
    )

    if not monitor.connect():
        log.error("Failed to connect to inverter")
        return 1

    log.info("Reading inverter data...")
    data = monitor.read_all()

    if data:
        log.info("Inverter data:")
        for key, value in data.items():
            log.info(f"  {key}: {value}")
    else:
        log.error("Failed to read inverter data")

    monitor.disconnect()
    return 0 if data else 1


def test_generator(config):
    """Test generator connection"""
    log.info("Testing generator connection...")

    client = GeneratorClient(
        config['generator_host'],
        config['generator_port']
    )

    try:
        client.connect()
        log.info("Connected to generator server")

        response = client.ping()
        log.info(f"PING response: {response}")

        response = client.status()
        log.info(f"STATUS:\n{response}")

        client.disconnect()
        return 0

    except Exception as e:
        log.error(f"Generator test failed: {e}")
        return 1


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='PiGenny Monitor')
    parser.add_argument('--test-inverter', action='store_true',
                       help='Test inverter connection only')
    parser.add_argument('--test-generator', action='store_true',
                       help='Test generator connection only')
    parser.add_argument('--inverter-port', default=CONFIG['inverter_port'],
                       help=f"Inverter serial port (default: {CONFIG['inverter_port']})")
    parser.add_argument('--inverter-baud', type=int, default=CONFIG['inverter_baud'],
                       help=f"Inverter baud rate (default: {CONFIG['inverter_baud']})")
    parser.add_argument('--generator-host', default=CONFIG['generator_host'],
                       help=f"Generator server host (default: {CONFIG['generator_host']})")
    parser.add_argument('--soc-start', type=int, default=CONFIG['soc_start_threshold'],
                       help=f"SOC threshold to start generator (default: {CONFIG['soc_start_threshold']})")
    parser.add_argument('--soc-stop', type=int, default=CONFIG['soc_stop_threshold'],
                       help=f"SOC threshold to stop generator (default: {CONFIG['soc_stop_threshold']})")
    parser.add_argument('--log-dir', default=CONFIG['csv_log_dir'],
                       help=f"CSV log directory (default: {CONFIG['csv_log_dir']})")
    parser.add_argument('--log-interval', type=int, default=CONFIG['log_interval'],
                       help=f"Seconds between CSV log entries (default: {CONFIG['log_interval']} = 10 min)")
    args = parser.parse_args()

    # Update config from args
    config = CONFIG.copy()
    config['inverter_port'] = args.inverter_port
    config['inverter_baud'] = args.inverter_baud
    config['generator_host'] = args.generator_host
    config['soc_start_threshold'] = args.soc_start
    config['soc_stop_threshold'] = args.soc_stop
    config['csv_log_dir'] = args.log_dir
    config['log_interval'] = args.log_interval

    if args.test_inverter:
        return test_inverter(config)
    elif args.test_generator:
        return test_generator(config)
    else:
        monitor = PiGennyMonitor(config)
        return monitor.run()


if __name__ == '__main__':
    sys.exit(main())
