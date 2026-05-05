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
import math
import json
import secrets
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
# Manual Control Files
# =============================================================================

FORCE_CHARGE_FILE = '/tmp/pigenny_force_charge'
FORCE_STOP_FILE = '/tmp/pigenny_force_stop'
FUEL_REFILL_FILE = '/tmp/pigenny_fuel_refilled'


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
    'soc_start_threshold': 40,    # Forecast zone: evaluate generator need below this SOC
    'soc_stop_threshold': 80,     # Stop generator when SOC rises above this
    'soc_reserve_threshold': 25,  # Hard reserve floor; avoid planning below this SOC
    'soc_reserve_buffer': 2,      # Extra SOC margin above reserve for forecast decisions
    'dynamic_charging_enabled': True,
    'generator_charge_rate_soc_per_hour': 19.0,  # Observed generator charge rate from logs
    'min_generator_charge_runtime': 1800,  # Minimum charger-enabled runtime, excluding warmup/cooldown
    'soc_slope_window': 10800,    # Seconds of recent low-solar SOC history for drain estimate
    'fallback_soc_drain_per_hour': 2.0,  # Used after restart before enough local history exists
    'max_soc_drain_per_hour': 6.0,       # Clamp extreme short-window estimates
    'solar_forecast_days': 21,
    'solar_forecast_percentile': 75,     # Use a conservative recent useful-solar start percentile
    'solar_history_refresh_interval': 21600,  # Refresh learned solar starts every 6 hours
    'solar_fallback_start_hour': 10,     # Fallback useful-solar start if CSV history is unavailable
    'solar_fallback_start_minute': 30,
    'solar_pv_power_threshold': 1000,    # Total PV power indicating useful solar
    'solar_charge_power_threshold': 200, # PV battery charge indicating useful solar

    # Timing
    'poll_interval': 30,          # Seconds between inverter reads
    'error_recovery_wait': 3600,  # Seconds to wait after 3 failed starts before retrying
    'generator_max_runtime': 14400, # Maximum seconds for generator to run (4 hours)

    # Safety
    'max_start_attempts': 3,      # Max consecutive start failures before giving up

    # Logging
    'csv_log_dir': '/var/log/pigenny',
    'csv_log_prefix': 'data_',
    'log_interval': 600,          # Seconds between CSV log entries (default 10 min)
    'olimex_health_check_interval': 3600,  # Seconds between Olimex health checks (default 1 hour)

    # Fuel tracking and alerts
    'fuel_tracking_enabled': True,
    'fuel_state_file': '/home/derekja/pigenny/fuel_state.json',
    'fuel_full_runtime_hours': 12.0,       # Estimated generator runtime from a full tank
    'fuel_alert_remaining_hours': 4.0,     # Alert when estimated runtime remaining drops below this
    'fuel_alert_retry_interval': 3600,     # Retry failed/misconfigured alerts at most hourly
    'fuel_reset_http_enabled': True,
    'fuel_reset_listen_host': '0.0.0.0',
    'fuel_reset_listen_port': 8765,
    'fuel_reset_base_url': os.environ.get('PIGENNY_FUEL_RESET_BASE_URL', 'http://10.147.18.216:8765'),
    'pushover_user_key': os.environ.get('PIGENNY_PUSHOVER_USER_KEY', ''),
    'pushover_app_token': os.environ.get('PIGENNY_PUSHOVER_APP_TOKEN', ''),
}


# =============================================================================
# CSV Logging
# =============================================================================

class CSVLogger:
    """Logs data to daily CSV files - opens, writes, closes on each entry"""

    CSV_FIELDS = [
        'timestamp', 'timestamp_unix', 'soc_pct', 'soh_pct', 'vbat_v',
        'vpv1_v', 'vpv2_v', 'pv1_power_w', 'pv2_power_w', 'load_power_w',
        'charge_power_w', 'discharge_power_w', 'generator_state', 'generator_running'
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
            'pv1_power_w': inverter_data.get('pv1_power', ''),
            'pv2_power_w': inverter_data.get('pv2_power', ''),
            'load_power_w': inverter_data.get('load_power', ''),
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
                'pv1_power': regs[7],
                'pv2_power': regs[8],
                'charge_power': regs[10],
                'discharge_power': regs[11],
            }

            # Read register 170 for load power (separate read since not contiguous)
            result2 = self.client.read_input_registers(170, count=1, slave=self.slave_id)
            if not result2.isError():
                data['load_power'] = result2.registers[0]
            else:
                data['load_power'] = 0  # Default if read fails

            return data

        except Exception as e:
            log.error(f"Error reading inverter data: {e}")
            return None


# =============================================================================
# Fuel Runtime Tracking
# =============================================================================

class FuelTracker:
    """Tracks estimated generator fuel remaining by accumulated runtime."""

    def __init__(self, config):
        self.enabled = config['fuel_tracking_enabled']
        self.state_file = config['fuel_state_file']
        self.full_runtime_seconds = int(config['fuel_full_runtime_hours'] * 3600)
        self.alert_remaining_seconds = int(config['fuel_alert_remaining_hours'] * 3600)
        self.alert_retry_interval = config['fuel_alert_retry_interval']
        self.reset_base_url = config['fuel_reset_base_url'].rstrip('/')
        self.pushover_user_key = config['pushover_user_key']
        self.pushover_app_token = config['pushover_app_token']
        self.state = self._load_state()

    def _default_state(self):
        now = datetime.now().isoformat(timespec='seconds')
        return {
            'runtime_seconds_since_refill': 0,
            'last_refill_at': now,
            'alert_sent': False,
            'last_alert_attempt_at': None,
            'reset_token': secrets.token_urlsafe(24),
        }

    def _load_state(self):
        if not self.enabled:
            return self._default_state()

        try:
            with open(self.state_file) as f:
                state = json.load(f)
        except FileNotFoundError:
            state = self._default_state()
            self._save_state(state)
        except Exception as e:
            log.warning(f"Unable to load fuel state {self.state_file}: {e}")
            state = self._default_state()

        state.setdefault('runtime_seconds_since_refill', 0)
        state.setdefault('last_refill_at', datetime.now().isoformat(timespec='seconds'))
        state.setdefault('alert_sent', False)
        state.setdefault('last_alert_attempt_at', None)
        if not state.get('reset_token'):
            state['reset_token'] = secrets.token_urlsafe(24)
            self._save_state(state)
        return state

    def _save_state(self, state=None):
        if not self.enabled:
            return

        if state is None:
            state = self.state

        try:
            state_dir = os.path.dirname(self.state_file)
            if state_dir and not os.path.exists(state_dir):
                os.makedirs(state_dir)

            temp_file = f"{self.state_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.write('\n')
            os.replace(temp_file, self.state_file)
        except Exception as e:
            log.warning(f"Unable to save fuel state {self.state_file}: {e}")

    def reset_full(self, reason):
        if not self.enabled:
            return

        token = self.state.get('reset_token') or secrets.token_urlsafe(24)
        self.state = self._default_state()
        self.state['reset_token'] = token
        self.state['last_refill_reason'] = reason
        self._save_state()
        log.info(
            f"Fuel runtime estimate reset to full tank ({self.full_runtime_seconds/3600:.1f}h): {reason}"
        )

    def reset_token(self):
        return self.state.get('reset_token', '')

    def reset_url(self):
        token = self.reset_token()
        if not self.reset_base_url or not token:
            return None

        return f"{self.reset_base_url}/fuel/refilled?token={urllib.parse.quote(token)}"

    def remaining_seconds(self):
        used = int(self.state.get('runtime_seconds_since_refill', 0))
        return max(0, self.full_runtime_seconds - used)

    def add_runtime(self, seconds, reason):
        if not self.enabled or seconds <= 0:
            return

        self.state['runtime_seconds_since_refill'] = (
            int(self.state.get('runtime_seconds_since_refill', 0)) + int(seconds)
        )
        self._save_state()

        log.info(
            f"Fuel estimate: added {seconds/60:.1f} runtime minutes ({reason}); "
            f"remaining {self.remaining_seconds()/3600:.1f}h"
        )
        self.check_alert()

    def check_refill_marker(self):
        if not self.enabled or not os.path.exists(FUEL_REFILL_FILE):
            return

        self.reset_full(f"marker file {FUEL_REFILL_FILE}")
        try:
            os.remove(FUEL_REFILL_FILE)
        except Exception as e:
            log.warning(f"Unable to remove fuel refill marker {FUEL_REFILL_FILE}: {e}")

    def _should_attempt_alert(self, now):
        last_attempt = self.state.get('last_alert_attempt_at')
        if not last_attempt:
            return True

        try:
            elapsed = (now - datetime.fromisoformat(last_attempt)).total_seconds()
            return elapsed >= self.alert_retry_interval
        except ValueError:
            return True

    def check_alert(self):
        if not self.enabled:
            return

        remaining = self.remaining_seconds()
        if remaining > self.alert_remaining_seconds or self.state.get('alert_sent'):
            return

        now = datetime.now()
        if not self._should_attempt_alert(now):
            return

        self.state['last_alert_attempt_at'] = now.isoformat(timespec='seconds')
        self._save_state()

        title = "PiGenny fuel warning"
        message = (
            f"Estimated generator fuel remaining is {remaining/3600:.1f}h, "
            f"below the {self.alert_remaining_seconds/3600:.1f}h alert threshold. "
            f"Last refill reset: {self.state.get('last_refill_at', 'unknown')}."
        )

        if self._send_pushover(title, message, self.reset_url()):
            self.state['alert_sent'] = True
            self._save_state()
            log.info("Fuel warning sent via Pushover")

    def _send_pushover(self, title, message, reset_url=None):
        if not self.pushover_user_key or not self.pushover_app_token:
            log.warning(
                "Fuel warning not sent: Pushover user key or app token is not configured"
            )
            return False

        payload_data = {
            'token': self.pushover_app_token,
            'user': self.pushover_user_key,
            'title': title,
            'message': message,
            'priority': 1,
        }
        if reset_url:
            payload_data['url'] = reset_url
            payload_data['url_title'] = 'Reset fuel estimate after refill'

        payload = urllib.parse.urlencode(payload_data).encode('utf-8')

        try:
            request = urllib.request.Request(
                'https://api.pushover.net/1/messages.json',
                data=payload,
                method='POST'
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                if 200 <= response.status < 300:
                    return True
                log.warning(f"Pushover returned HTTP {response.status}")
        except Exception as e:
            log.warning(f"Failed to send Pushover fuel warning: {e}")

        return False


class FuelResetServer:
    """Tiny HTTP server for phone-triggered fuel refill resets."""

    def __init__(self, tracker, host, port):
        self.tracker = tracker
        self.host = host
        self.port = port
        self.httpd = None
        self.thread = None

    def start(self):
        tracker = self.tracker

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def log_request(self, code='-', size='-'):
                path = urllib.parse.urlparse(self.path).path
                log.info("Fuel reset HTTP: %s %s -> %s", self.command, path, code)

            def _send_text(self, status, body):
                encoded = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                token = params.get('token', [''])[0]

                if parsed.path not in ('/fuel/refilled', '/fuel/status'):
                    self._send_text(404, "PiGenny fuel endpoint not found.\n")
                    return

                if not token or not secrets.compare_digest(token, tracker.reset_token()):
                    self._send_text(403, "Invalid fuel reset token.\n")
                    return

                if parsed.path == '/fuel/status':
                    self._send_text(
                        200,
                        f"Estimated fuel remaining: {tracker.remaining_seconds()/3600:.1f}h\n"
                    )
                    return

                tracker.reset_full(
                    f"HTTP reset from {self.client_address[0]}"
                )
                self._send_text(
                    200,
                    "PiGenny fuel estimate reset to full. You can close this page.\n"
                )

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name='fuel-reset-http',
            daemon=True
        )
        self.thread.start()
        log.info(f"Fuel reset HTTP server listening on {self.host}:{self.port}")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


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
    STATE_ERROR_RECOVERY = 'ERROR_RECOVERY'
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

        # Fuel runtime tracking
        self.fuel_tracker = FuelTracker(config)
        self.fuel_reset_server = None
        if config['fuel_tracking_enabled'] and config['fuel_reset_http_enabled']:
            self.fuel_reset_server = FuelResetServer(
                self.fuel_tracker,
                config['fuel_reset_listen_host'],
                config['fuel_reset_listen_port']
            )
        self.last_fuel_runtime_update_at = datetime.now()
        self.last_fuel_runtime_active = False

        # Olimex health monitoring
        self.olimex_health_check_interval = config['olimex_health_check_interval']
        self.last_health_check_time = None

        # Timing
        self.generator_started_at = None
        self.generator_stopped_at = None
        self.start_attempts = 0
        self.error_recovery_started_at = None

        # Last readings
        self.last_soc = None
        self.last_voltage = None
        self.reading_history = []
        self.solar_start_minutes = []
        self.last_solar_history_refresh_at = None
        self._refresh_solar_start_history(datetime.now(), force=True)

        # Manual control mode
        self.manual_mode = False
        self.dynamic_charge_target_soc = None
        self.dynamic_charge_reason = None

    def _is_useful_solar(self, data):
        """Return True when solar is already contributing enough to defer generator starts."""
        pv_total = data.get('pv1_power', 0) + data.get('pv2_power', 0)
        return (
            pv_total >= self.config['solar_pv_power_threshold'] and
            data.get('charge_power', 0) >= self.config['solar_charge_power_threshold']
        )

    def _percentile(self, values, percentile):
        if not values:
            return None

        values = sorted(values)
        if len(values) == 1:
            return values[0]

        rank = (len(values) - 1) * (percentile / 100.0)
        low = int(math.floor(rank))
        high = int(math.ceil(rank))
        if low == high:
            return values[low]

        weight = rank - low
        return values[low] * (1 - weight) + values[high] * weight

    def _load_solar_start_history(self):
        """Load recent useful-solar start times from CSV logs for local time forecasting."""
        log_dir = self.config['csv_log_dir']
        if not os.path.isdir(log_dir):
            return []

        try:
            csv_files = sorted([
                os.path.join(log_dir, name)
                for name in os.listdir(log_dir)
                if name.startswith(self.config['csv_log_prefix']) and name.endswith('.csv')
            ])[-self.config['solar_forecast_days']:]
        except Exception as e:
            log.warning(f"Unable to list CSV logs for solar forecast: {e}")
            return []

        start_minutes = []
        for path in csv_files:
            try:
                with open(path, newline='') as csvfile:
                    rows = []
                    for row in csv.DictReader(csvfile):
                        dt = datetime.fromisoformat(row['timestamp'])
                        if dt.hour < 4:
                            continue
                        try:
                            pv_total = int(row.get('pv1_power_w') or 0) + int(row.get('pv2_power_w') or 0)
                            charge_power = int(row.get('charge_power_w') or 0)
                        except ValueError:
                            continue

                        rows.append((dt, pv_total, charge_power))

                    for i in range(0, max(0, len(rows) - 2)):
                        window = rows[i:i + 3]
                        if all(
                            pv >= self.config['solar_pv_power_threshold'] and
                            charge >= self.config['solar_charge_power_threshold']
                            for _, pv, charge in window
                        ):
                            start = window[0][0]
                            start_minutes.append(start.hour * 60 + start.minute)
                            break
            except Exception as e:
                log.warning(f"Unable to read {path} for solar forecast: {e}")

        return start_minutes

    def _refresh_solar_start_history(self, now, force=False):
        """Refresh learned solar start times so the forecast follows seasonal drift."""
        if (
            not force and
            self.last_solar_history_refresh_at is not None and
            (now - self.last_solar_history_refresh_at).total_seconds() <
            self.config['solar_history_refresh_interval']
        ):
            return

        previous = self.solar_start_minutes
        refreshed = self._load_solar_start_history()
        if refreshed:
            self.solar_start_minutes = refreshed
            forecast_minute = int(round(self._percentile(
                self.solar_start_minutes,
                self.config['solar_forecast_percentile']
            )))
            log.info(
                f"Solar forecast history refreshed: {len(refreshed)} days, "
                f"p{self.config['solar_forecast_percentile']} useful solar start "
                f"{forecast_minute // 60:02d}:{forecast_minute % 60:02d}"
            )
        elif previous:
            log.warning("Solar forecast refresh found no usable CSV history; keeping previous forecast")
        else:
            log.warning("Solar forecast has no CSV history; using fallback useful-solar time")

        self.last_solar_history_refresh_at = now

    def _forecast_solar_start(self, now):
        """Forecast the next useful solar start as a local datetime."""
        self._refresh_solar_start_history(now)

        if self.solar_start_minutes:
            minute_of_day = int(round(self._percentile(
                self.solar_start_minutes,
                self.config['solar_forecast_percentile']
            )))
        else:
            minute_of_day = (
                self.config['solar_fallback_start_hour'] * 60 +
                self.config['solar_fallback_start_minute']
            )

        minute_of_day = max(0, min(23 * 60 + 59, minute_of_day))
        forecast = now.replace(
            hour=minute_of_day // 60,
            minute=minute_of_day % 60,
            second=0,
            microsecond=0
        )

        if now >= forecast:
            forecast += timedelta(days=1)

        return forecast

    def _record_reading(self, data, now):
        """Keep a short in-memory history for SOC drain estimates."""
        self.reading_history.append({
            'timestamp': now,
            'soc': data['soc'],
            'pv_total': data.get('pv1_power', 0) + data.get('pv2_power', 0),
            'charge_power': data.get('charge_power', 0),
            'discharge_power': data.get('discharge_power', 0),
            'generator_active': self.state in (
                self.STATE_STARTING,
                self.STATE_RUNNING,
                self.STATE_STOPPING
            )
        })

        cutoff = now - timedelta(seconds=max(
            self.config['soc_slope_window'],
            self.config['log_interval']
        ))
        self.reading_history = [
            reading for reading in self.reading_history
            if reading['timestamp'] >= cutoff
        ]

    def _estimate_soc_slope_per_hour(self, now):
        """Estimate current low-solar SOC slope in %/hour. Negative means draining."""
        cutoff = now - timedelta(seconds=self.config['soc_slope_window'])
        samples = [
            reading for reading in self.reading_history
            if reading['timestamp'] >= cutoff and
            not reading['generator_active'] and
            reading['pv_total'] < self.config['solar_pv_power_threshold'] and
            reading['charge_power'] <= self.config['solar_charge_power_threshold']
        ]

        if len(samples) >= 4:
            elapsed_hours = (
                samples[-1]['timestamp'] - samples[0]['timestamp']
            ).total_seconds() / 3600.0
            if elapsed_hours > 0:
                slope = (samples[-1]['soc'] - samples[0]['soc']) / elapsed_hours
                if slope < 0:
                    max_drain = self.config['max_soc_drain_per_hour']
                    return max(-max_drain, slope)

        return -self.config['fallback_soc_drain_per_hour']

    def _dynamic_start_decision(self, data, now):
        """Decide whether to start and what dynamic SOC target to use."""
        soc = data['soc']
        reserve = self.config['soc_reserve_threshold']
        reserve_target = reserve + self.config['soc_reserve_buffer']

        if not self.config['dynamic_charging_enabled']:
            if soc < self.config['soc_start_threshold']:
                return True, self.config['soc_stop_threshold'], "static threshold"
            return False, None, "above static threshold"

        if soc <= reserve:
            min_gain = (
                self.config['min_generator_charge_runtime'] / 3600.0 *
                self.config['generator_charge_rate_soc_per_hour']
            )
            target_soc = min(
                self.config['soc_stop_threshold'],
                max(reserve_target, soc + min_gain)
            )
            return True, target_soc, "at or below reserve"

        if soc >= self.config['soc_start_threshold']:
            return False, None, "above forecast zone"

        if self._is_useful_solar(data):
            return False, None, "useful solar already active"

        solar_start = self._forecast_solar_start(now)
        hours_to_solar = max(0.0, (solar_start - now).total_seconds() / 3600.0)
        slope = self._estimate_soc_slope_per_hour(now)
        projected_soc = soc + (slope * hours_to_solar)

        if projected_soc >= reserve_target:
            log.info(
                "Dynamic charge defer: SOC %.1f%%, slope %.2f%%/h, solar in %.1fh, "
                "projected %.1f%% >= reserve target %.1f%%",
                soc, slope, hours_to_solar, projected_soc, reserve_target
            )
            return False, None, "forecast above reserve"

        required_gain = reserve_target - projected_soc
        min_gain = (
            self.config['min_generator_charge_runtime'] / 3600.0 *
            self.config['generator_charge_rate_soc_per_hour']
        )
        target_soc = min(
            self.config['soc_stop_threshold'],
            max(soc + required_gain, soc + min_gain, reserve_target)
        )

        log.info(
            "Dynamic charge start: SOC %.1f%%, slope %.2f%%/h, solar in %.1fh, "
            "projected %.1f%% < reserve target %.1f%%, target %.1f%%",
            soc, slope, hours_to_solar, projected_soc, reserve_target, target_soc
        )
        return True, target_soc, "forecast below reserve"

    def check_force_charge(self):
        """Check if force charge file exists"""
        return os.path.exists(FORCE_CHARGE_FILE)

    def check_force_stop(self):
        """Check if force stop file exists"""
        return os.path.exists(FORCE_STOP_FILE)

    def _account_fuel_runtime(self, now):
        """Account generator runtime between monitor cycles."""
        if self.last_fuel_runtime_update_at is None:
            self.last_fuel_runtime_update_at = now
            self.last_fuel_runtime_active = self.state == self.STATE_RUNNING
            return

        elapsed = (now - self.last_fuel_runtime_update_at).total_seconds()
        if self.last_fuel_runtime_active and elapsed > 0:
            self.fuel_tracker.add_runtime(elapsed, "running")

        self.last_fuel_runtime_update_at = now
        self.last_fuel_runtime_active = self.state == self.STATE_RUNNING

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
        if self.fuel_reset_server:
            self.fuel_reset_server.stop()
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

    def start_generator(self, target_soc=None, reason=None):
        """Start the generator"""
        log.info("Starting generator...")
        self.state = self.STATE_STARTING
        command_started_at = datetime.now()

        try:
            response = self.generator.start()
            command_finished_at = datetime.now()
            log.info(f"Start response: {response}")

            if response.startswith("OK:"):
                self.fuel_tracker.add_runtime(
                    (command_finished_at - command_started_at).total_seconds(),
                    "start/warmup sequence"
                )
                self.state = self.STATE_RUNNING
                self.generator_started_at = command_finished_at
                self.last_fuel_runtime_update_at = command_finished_at
                self.last_fuel_runtime_active = True
                self.start_attempts = 0
                self.dynamic_charge_target_soc = target_soc
                self.dynamic_charge_reason = reason
                log.info("Generator started successfully")
                if target_soc is not None:
                    log.info(
                        f"Dynamic charge target set to {target_soc:.1f}% "
                        f"({reason or 'no reason recorded'})"
                    )
                return True
            else:
                self.start_attempts += 1
                self.dynamic_charge_target_soc = None
                self.dynamic_charge_reason = None
                log.error(f"Generator start failed: {response}")
                self.state = self.STATE_ERROR if self.start_attempts >= self.config['max_start_attempts'] else self.STATE_IDLE
                return False

        except Exception as e:
            self.start_attempts += 1
            self.dynamic_charge_target_soc = None
            self.dynamic_charge_reason = None
            log.error(f"Generator start exception: {e}")
            self.state = self.STATE_ERROR if self.start_attempts >= self.config['max_start_attempts'] else self.STATE_IDLE
            return False

    def stop_generator(self):
        """Stop the generator (normal stop - goes to IDLE when done)"""
        log.info("Stopping generator...")
        now = datetime.now()
        self._account_fuel_runtime(now)
        self.state = self.STATE_STOPPING
        command_started_at = now

        try:
            response = self.generator.stop()
            command_finished_at = datetime.now()
            log.info(f"Stop response: {response}")
            self.fuel_tracker.add_runtime(
                (command_finished_at - command_started_at).total_seconds(),
                "stop/cooldown sequence"
            )

            self.state = self.STATE_IDLE
            self.generator_stopped_at = command_finished_at
            self.generator_started_at = None
            self.last_fuel_runtime_update_at = command_finished_at
            self.last_fuel_runtime_active = False
            self.dynamic_charge_target_soc = None
            self.dynamic_charge_reason = None
            return True

        except Exception as e:
            command_finished_at = datetime.now()
            log.error(f"Generator stop exception: {e}")
            # Even on error, transition to idle - generator likely already stopped
            # or connection failed. Better to go to idle than stay stuck in STOPPING.
            self.state = self.STATE_IDLE
            self.generator_stopped_at = command_finished_at
            self.generator_started_at = None
            self.last_fuel_runtime_update_at = command_finished_at
            self.last_fuel_runtime_active = False
            self.dynamic_charge_target_soc = None
            self.dynamic_charge_reason = None
            return False

    def check_error_recovery_wait(self):
        """Check if error recovery wait period has passed (1 hour after 3 failed starts)"""
        if self.error_recovery_started_at is None:
            return True

        elapsed = (datetime.now() - self.error_recovery_started_at).total_seconds()
        if elapsed >= self.config['error_recovery_wait']:
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
            status_text = self.generator.status()
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
        now = datetime.now()
        self.fuel_tracker.check_refill_marker()
        self._account_fuel_runtime(now)

        # Read inverter
        data = self.inverter.read_all()
        if data:
            self.last_soc = data['soc']
            self.last_voltage = data['battery_voltage']
            log.info(f"SOC: {data['soc']}% | Voltage: {data['battery_voltage']}V | "
                    f"PV1: {data['pv1_power']}W | PV2: {data['pv2_power']}W | "
                    f"Load: {data['load_power']}W | Charge: {data['charge_power']}W | "
                    f"Discharge: {data['discharge_power']}W")

            # Log to CSV at specified interval
            self._record_reading(data, now)
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

        # Check manual control files
        force_charge = self.check_force_charge()
        force_stop = self.check_force_stop()

        # State machine
        if self.state == self.STATE_IDLE:
            # Check for manual force charge
            if force_charge:
                log.info("Force charge file detected - starting generator (manual mode)")
                self.manual_mode = True
                self.start_generator()
            # Check if we need to start based on forecasted reserve need
            else:
                should_start, target_soc, reason = self._dynamic_start_decision(data, now)
                if should_start:
                    self.manual_mode = False
                    self.start_generator(target_soc=target_soc, reason=reason)

        elif self.state == self.STATE_RUNNING:
            # Check for manual force stop (highest priority)
            if force_stop:
                log.info("Force stop file detected - stopping generator (manual override)")
                self.manual_mode = False
                self.stop_generator()
                # Remove force_stop file so normal operation resumes
                try:
                    os.remove(FORCE_STOP_FILE)
                    log.info("Force stop file removed - normal operation will resume")
                except:
                    pass
            # Check if manual mode was cancelled (force_charge file removed)
            elif self.manual_mode and not force_charge:
                log.info("Force charge file removed - stopping generator (exiting manual mode)")
                self.manual_mode = False
                self.stop_generator()
            # Check if generator unexpectedly stopped (fuel out, stall, etc)
            elif not self.is_generator_running():
                log.error("Generator stopped unexpectedly (fuel out, stall, or mechanical failure)")
                log.info("Entering error recovery mode - will attempt restarts")
                # Clear relays via stop command, then enter error recovery
                try:
                    self.generator.stop()
                except:
                    pass  # Best effort to clear relays
                self.state = self.STATE_ERROR_RECOVERY
                self.error_recovery_started_at = datetime.now()
                self.generator_started_at = None
                self.dynamic_charge_target_soc = None
                self.dynamic_charge_reason = None
                self.manual_mode = False
                # Don't reset start_attempts - let it accumulate
            # Check if we should stop based on SOC (only if not in manual mode)
            elif not self.manual_mode and soc >= self.config['soc_stop_threshold']:
                log.info(f"SOC {soc}% reached threshold {self.config['soc_stop_threshold']}% - stopping generator")
                self.stop_generator()
            elif not self.manual_mode and self.dynamic_charge_target_soc is not None:
                elapsed = 0
                if self.generator_started_at is not None:
                    elapsed = (datetime.now() - self.generator_started_at).total_seconds()

                if soc >= self.dynamic_charge_target_soc and elapsed >= self.config['min_generator_charge_runtime']:
                    log.info(
                        f"Dynamic charge target {self.dynamic_charge_target_soc:.1f}% reached "
                        f"after {elapsed/60:.0f} charger-enabled minutes - stopping generator"
                    )
                    self.stop_generator()
                elif soc >= self.dynamic_charge_target_soc:
                    remaining = self.config['min_generator_charge_runtime'] - elapsed
                    log.info(
                        f"Dynamic charge target {self.dynamic_charge_target_soc:.1f}% reached, "
                        f"holding for minimum runtime ({remaining/60:.0f} min remaining)"
                    )
            elif self.check_max_runtime():
                log.warning("Generator max runtime exceeded - stopping")
                self.manual_mode = False
                self.stop_generator()

        elif self.state == self.STATE_STOPPING:
            # Verify generator has stopped and transition to idle
            # This state should be transient (stop_generator sets it then immediately transitions)
            # but if we're stuck here, verify and move on to prevent infinite hang
            try:
                status = self.generator.status()
                if status and "RUNNING: NO" in status:
                    log.info("Verified generator stopped, transitioning to idle")
                    self.state = self.STATE_IDLE
                    if self.generator_stopped_at is None:
                        self.generator_stopped_at = datetime.now()
                        self.generator_started_at = None
                    self.dynamic_charge_target_soc = None
                    self.dynamic_charge_reason = None
                else:
                    log.warning("Still in STOPPING state - generator may still be running")
                    # Transition to idle anyway after one check to avoid infinite loop
                    self.state = self.STATE_IDLE
                    if self.generator_stopped_at is None:
                        self.generator_stopped_at = datetime.now()
                        self.generator_started_at = None
                    self.dynamic_charge_target_soc = None
                    self.dynamic_charge_reason = None
            except Exception as e:
                log.warning(f"Failed to verify generator status in STOPPING state: {e}")
                # Can't verify, assume stopped and transition to idle
                self.state = self.STATE_IDLE
                if self.generator_stopped_at is None:
                    self.generator_stopped_at = datetime.now()
                    self.generator_started_at = None
                self.dynamic_charge_target_soc = None
                self.dynamic_charge_reason = None

        elif self.state == self.STATE_ERROR_RECOVERY:
            # Error recovery: try to restart, with rate limiting after 3 failures
            if self.start_attempts >= self.config['max_start_attempts']:
                # Already tried 3 times, check if wait period has passed
                if self.check_error_recovery_wait():
                    log.info("Error recovery wait complete, resetting attempts and retrying")
                    self.start_attempts = 0
                    self.error_recovery_started_at = None
                    # Will attempt start on next cycle
                else:
                    elapsed = (datetime.now() - self.error_recovery_started_at).total_seconds()
                    remaining = self.config['error_recovery_wait'] - elapsed
                    log.warning(f"In error recovery - {self.start_attempts} failed attempts, "
                               f"waiting {remaining/60:.0f} more minutes before retry")
            else:
                # Still have attempts remaining, try to start if the forecast says we must
                should_start, target_soc, reason = self._dynamic_start_decision(data, now)
                if should_start:
                    log.info(f"Error recovery: attempting restart (attempt {self.start_attempts + 1})")
                    if self.start_generator(target_soc=target_soc, reason=reason):
                        # Success! Clear error recovery state
                        self.error_recovery_started_at = None
                    elif self.start_attempts >= self.config['max_start_attempts']:
                        # Just hit max attempts, start the wait timer
                        log.error(f"Error recovery: {self.start_attempts} consecutive failures, "
                                 f"waiting {self.config['error_recovery_wait']/60:.0f} minutes before retry")
                        self.error_recovery_started_at = datetime.now()
                else:
                    # SOC recovered (maybe from solar), exit error recovery
                    log.info(f"SOC {soc}% recovered above threshold, exiting error recovery")
                    self.state = self.STATE_IDLE
                    self.start_attempts = 0
                    self.error_recovery_started_at = None
                    self.dynamic_charge_target_soc = None
                    self.dynamic_charge_reason = None

        elif self.state == self.STATE_ERROR:
            log.error(f"In error state after {self.start_attempts} failed start attempts")
            # Legacy error state - shouldn't reach here with new logic

        mode_str = " (MANUAL)" if self.manual_mode else ""
        log.info(f"State: {self.state}{mode_str}")

    def run(self):
        """Main monitoring loop"""
        log.info("Starting PiGenny monitor...")
        log.info(f"SOC thresholds: forecast zone < {self.config['soc_start_threshold']}%, "
                f"reserve {self.config['soc_reserve_threshold']}%, "
                f"stop >= {self.config['soc_stop_threshold']}%")
        if self.config['dynamic_charging_enabled']:
            solar_start = self._forecast_solar_start(datetime.now())
            log.info(
                f"Dynamic charging enabled: reserve buffer {self.config['soc_reserve_buffer']}%, "
                f"min charger-enabled runtime {self.config['min_generator_charge_runtime']/60:.0f} min, "
                f"generator charge rate {self.config['generator_charge_rate_soc_per_hour']:.1f}%/h, "
                f"next useful solar forecast {solar_start.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            log.info("Dynamic charging disabled - using static SOC start/stop thresholds")
        if self.config['fuel_tracking_enabled']:
            log.info(
                f"Fuel tracking enabled: full tank {self.config['fuel_full_runtime_hours']:.1f}h, "
                f"alert below {self.config['fuel_alert_remaining_hours']:.1f}h remaining, "
                f"state file {self.config['fuel_state_file']}, refill marker {FUEL_REFILL_FILE}"
            )
            if self.fuel_reset_server:
                self.fuel_reset_server.start()
                reset_url = self.fuel_tracker.reset_url()
                if reset_url:
                    log.info(
                        f"Fuel reset URL configured for Pushover alerts: "
                        f"{self.config['fuel_reset_base_url']}/fuel/refilled?token=..."
                    )
        log.info(f"Poll interval: {self.config['poll_interval']}s, "
                f"CSV log interval: {self.log_interval}s ({self.log_interval/60:.0f} min)")
        log.info(f"CSV log directory: {self.config['csv_log_dir']}")
        log.info(f"Manual control: touch {FORCE_CHARGE_FILE} to start, "
                f"rm to stop, or touch {FORCE_STOP_FILE} to force stop")

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
                       help=f"SOC forecast zone threshold (default: {CONFIG['soc_start_threshold']})")
    parser.add_argument('--soc-stop', type=int, default=CONFIG['soc_stop_threshold'],
                       help=f"SOC threshold to stop generator (default: {CONFIG['soc_stop_threshold']})")
    parser.add_argument('--soc-reserve', type=int, default=CONFIG['soc_reserve_threshold'],
                       help=f"Hard reserve SOC floor for dynamic charging (default: {CONFIG['soc_reserve_threshold']})")
    parser.add_argument('--disable-dynamic-charging', action='store_true',
                       help='Disable dynamic forecast charging and use static SOC thresholds')
    parser.add_argument('--min-generator-charge-runtime', type=int,
                       default=CONFIG['min_generator_charge_runtime'],
                       help='Minimum charger-enabled generator runtime in seconds for dynamic starts '
                            f"(default: {CONFIG['min_generator_charge_runtime']})")
    parser.add_argument('--generator-charge-rate', type=float,
                       default=CONFIG['generator_charge_rate_soc_per_hour'],
                       help='Estimated generator SOC charge rate in percent per hour '
                            f"(default: {CONFIG['generator_charge_rate_soc_per_hour']})")
    parser.add_argument('--disable-fuel-tracking', action='store_true',
                       help='Disable runtime-based fuel tracking and alerts')
    parser.add_argument('--fuel-full-runtime-hours', type=float,
                       default=CONFIG['fuel_full_runtime_hours'],
                       help='Estimated generator runtime from a full tank in hours '
                            f"(default: {CONFIG['fuel_full_runtime_hours']})")
    parser.add_argument('--fuel-alert-remaining-hours', type=float,
                       default=CONFIG['fuel_alert_remaining_hours'],
                       help='Send fuel warning below this many estimated runtime hours remaining '
                            f"(default: {CONFIG['fuel_alert_remaining_hours']})")
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
    config['soc_reserve_threshold'] = args.soc_reserve
    config['dynamic_charging_enabled'] = not args.disable_dynamic_charging
    config['min_generator_charge_runtime'] = args.min_generator_charge_runtime
    config['generator_charge_rate_soc_per_hour'] = args.generator_charge_rate
    config['fuel_tracking_enabled'] = not args.disable_fuel_tracking
    config['fuel_full_runtime_hours'] = args.fuel_full_runtime_hours
    config['fuel_alert_remaining_hours'] = args.fuel_alert_remaining_hours
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
