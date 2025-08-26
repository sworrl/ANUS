#!/usr/bin/env python3
# A.N.U.S. v1.3.9 - Service Update

import subprocess
import os
import time
import json
import sqlite3
import re
from concurrent.futures import ThreadPoolExecutor
import threading
import signal
import socket
import xml.etree.ElementTree as ET

# --- Configuration ---
DB_PATH = '/var/db/anus_metrics.db'
CLIENT_IP_FILE = '/var/db/anus_client_ip.txt'
TARGETS_CONFIG_FILE = '/var/www/html/anus/assets/targets.json'
NET_STATS_CACHE = '/var/tmp/anus_net_stats.json'
SOCKET_PATH = '/var/run/anus_service_cmd.sock'
try:
    # Attempt to dynamically find the default network interface
    output = subprocess.check_output("ip route | grep default | awk '{print $5}' | head -n 1", shell=True, text=True).strip()
    NETWORK_INTERFACE = output.splitlines()[0]
except Exception:
    NETWORK_INTERFACE = 'eth0' # Fallback to a common default

# --- Global State ---
stop_event = threading.Event()
GATEWAY_DETAILS = {'ip': 'N/A', 'mac': 'N/A', 'vendor': 'N/A'}
# Default settings, will be overwritten by DB
SETTINGS = {
    'updateInterval': 2000,
    'onlineDetectionMethod': 'smart_check',
    'smartCheckThreshold': 3,
    'criticalServices': 'Gateway,OpenDNS Primary,Google DNS,Cloudflare DNS'
}
MAX_WORKERS = 100

# --- Database Functions ---
def setup_database():
    """Initializes the database schema if tables don't exist and performs migrations."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Metrics for individual targets
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL,
                status TEXT, dns_info TEXT, traceroute_info TEXT,
                timestamp INTEGER, packet_loss REAL, is_on_demand BOOLEAN DEFAULT 0
            )
        """)
        
        # Add new columns if they don't exist (migration step)
        try:
            cursor.execute("ALTER TABLE metrics ADD COLUMN min_ping_15m REAL")
            cursor.execute("ALTER TABLE metrics ADD COLUMN max_ping_15m REAL")
            cursor.execute("ALTER TABLE metrics ADD COLUMN packet_loss_15m REAL")
            print("Database migration: Added 15m ping and loss columns to 'metrics' table.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                print(f"Migration error: {e}")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics (name, timestamp DESC)")
        # Server resource usage metrics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resource_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,
                cpu_usage REAL, mem_usage REAL, net_down_kbps REAL, net_up_kbps REAL
            )
        """)
        # Cached network info
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS local_network_info (
                id INTEGER PRIMARY KEY, gateway_ip TEXT UNIQUE, gateway_mac TEXT,
                gateway_vendor TEXT, last_updated INTEGER
            )
        """)
        # Nmap scan results
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nmap_scan_results (
                id INTEGER PRIMARY KEY, ip TEXT UNIQUE, mac_address TEXT,
                vendor TEXT, hostname TEXT, is_up BOOLEAN,
                services TEXT, os TEXT, last_scanned INTEGER
            )
        """)
        # Application settings table
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        # NEW: Event log for overall UP/DOWN status changes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                startTime INTEGER NOT NULL,
                endTime INTEGER
            )
        """)
        conn.commit()

def log_metric(metric):
    """Logs a single ping result to the metrics table."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Calculate 15m metrics
        now = int(time.time())
        time_15m_ago = now - 900
        
        cursor.execute("""
            SELECT AVG(ping), MIN(ping), MAX(ping), AVG(packet_loss)
            FROM metrics
            WHERE name = ? AND timestamp >= ?
        """, (metric['name'], time_15m_ago))
        
        avg_ping_15m, min_ping_15m, max_ping_15m, avg_packet_loss_15m = cursor.fetchone()
        
        if metric['status'] == 'UP':
            # Add current ping to average calculation
            ping_values = [p for p in [avg_ping_15m, metric['ping']] if p is not None]
            min_ping_15m = min(ping_values) if ping_values else None
            max_ping_15m = max(ping_values) if ping_values else None
            # The packet loss for the last 15 minutes is a more complex calculation
            packet_loss_15m = avg_packet_loss_15m if avg_packet_loss_15m is not None else 0
        else:
            packet_loss_15m = avg_packet_loss_15m if avg_packet_loss_15m is not None else 100
        
        cursor.execute("""
            INSERT INTO metrics (name, ping, jitter, status, dns_info, traceroute_info, timestamp, packet_loss, is_on_demand, min_ping_15m, max_ping_15m, packet_loss_15m)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metric['name'], metric.get('ping'), metric.get('jitter'), metric['status'],
            json.dumps(metric.get('dns_info', [])), json.dumps(metric.get('traceroute_info', [])),
            now, metric.get('packet_loss'), metric.get('is_on_demand', 0),
            min_ping_15m, max_ping_15m, packet_loss_15m
        ))
        conn.commit()

def log_resource_usage(usage_data):
    """Logs CPU, memory, and network usage to the database."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO resource_metrics (timestamp, cpu_usage, mem_usage, net_down_kbps, net_up_kbps)
            VALUES (?, ?, ?, ?, ?)
        """, (
            int(time.time()), usage_data['cpu'], usage_data['mem'],
            usage_data['net_down'], usage_data['net_up']
        ))
        conn.commit()

def update_event_log(current_status):
    """Logs a change in the overall internet status to the event_log table."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM event_log ORDER BY startTime DESC LIMIT 1")
        last_event = cursor.fetchone()

        if not last_event:
            # First ever event, log the startup status
            print(f"Logging initial status: {current_status}")
            cursor.execute("INSERT INTO event_log (status, startTime) VALUES (?, ?)", (current_status, int(time.time())))
        elif last_event[1] != current_status:
            # Status has changed, end the last event and start a new one
            print(f"Status changed from {last_event[1]} to {current_status}. Logging event.")
            cursor.execute("UPDATE event_log SET endTime = ? WHERE id = ?", (int(time.time()), last_event[0]))
            cursor.execute("INSERT INTO event_log (status, startTime) VALUES (?, ?)", (current_status, int(time.time())))
        conn.commit()

# --- Status Calculation ---
def calculate_internet_status(latest_metrics, settings):
    """
    Determines the overall internet status based on the configured detection method.
    This logic is mirrored from ping.php to ensure consistency.
    """
    method = settings.get('onlineDetectionMethod', 'smart_check')
    critical_services_str = settings.get('criticalServices', '')
    critical_services = [s.strip() for s in critical_services_str.split(',') if s.strip()]

    up_count = 0
    down_count = 0
    critical_up_count = 0
    critical_down_count = 0
    gateway_status = 'DOWN'

    for metric in latest_metrics:
        is_critical = metric['name'] in critical_services
        if metric['status'] == 'UP':
            up_count += 1
            if is_critical:
                critical_up_count += 1
        else:
            down_count += 1
            if is_critical:
                critical_down_count += 1
        if metric['name'] == 'Gateway':
            gateway_status = metric['status']

    if method == 'gateway':
        return gateway_status
    elif method == 'majority':
        return 'UP' if up_count >= down_count else 'DOWN'
    elif method == 'critical_services':
        return 'UP' if critical_down_count == 0 and critical_services else 'DOWN'
    else:  # smart_check is the default
        if gateway_status == 'DOWN':
            return 'DOWN'
        smart_threshold = int(settings.get('smartCheckThreshold', 3))
        if critical_down_count >= smart_threshold:
            return 'DOWN'
        return 'UP'

# --- System & Network Diagnostics ---
def get_resource_usage():
    """Continuously monitors and logs server resource usage."""
    while not stop_event.is_set():
        try:
            # CPU Usage
            load_avg = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            cpu_usage = (load_avg / cpu_count) * 100
            
            # Memory Usage
            mem_info = subprocess.check_output("free -m", shell=True, text=True).splitlines()[1].split()
            mem_total, mem_used = int(mem_info[1]), int(mem_info[2])
            mem_usage = (mem_used / mem_total) * 100 if mem_total else 0
            
            # Network Usage
            rx_rate, tx_rate = 0, 0
            now = time.time()
            try:
                with open(f"/sys/class/net/{NETWORK_INTERFACE}/statistics/rx_bytes") as f: rx_bytes = int(f.read())
                with open(f"/sys/class/net/{NETWORK_INTERFACE}/statistics/tx_bytes") as f: tx_bytes = int(f.read())
                last_stats = {}
                if os.path.exists(NET_STATS_CACHE):
                    with open(NET_STATS_CACHE, 'r') as f: last_stats = json.load(f)
                if 'timestamp' in last_stats and (time_diff := now - last_stats['timestamp']) > 0:
                    rx_rate = ((rx_bytes - last_stats['rx']) * 8) / time_diff / 1024 # Kbps
                    tx_rate = ((tx_bytes - last_stats['tx']) * 8) / time_diff / 1024 # Kbps
                with open(NET_STATS_CACHE, 'w') as f: json.dump({'rx': rx_bytes, 'tx': tx_bytes, 'timestamp': now}, f)
            except (IOError, ValueError, FileNotFoundError): pass

            log_resource_usage({
                'cpu': round(cpu_usage, 2), 'mem': round(mem_usage, 2),
                'net_down': round(rx_rate, 2), 'net_up': round(tx_rate, 2)
            })
        except Exception as e:
            print(f"Error in get_resource_usage: {e}")
        stop_event.wait(5)

def get_server_gateway_ip():
    """Finds the default gateway IP for the server."""
    try:
        output = subprocess.check_output("ip route | grep default | awk '{print $3}' | head -n 1", shell=True, text=True).strip()
        return output.splitlines()[0]
    except Exception: return None

def ping_target_once(target, server_gateway_ip):
    """Performs a single ping test against a target and returns the result dictionary."""
    is_gateway = target.get('url') == 'DETECT_GATEWAY'
    host_url = server_gateway_ip if is_gateway else target['url']
    clean_host = host_url.replace("https://", "").replace("http://", "").split('/')[0] if host_url else None
    
    # Define a default failure state to ensure all keys are present
    failure_result = {
        "name": target["name"], "status": "DOWN", "packet_loss": 100.0,
        "ping": None, "jitter": None, "dns_info": [], "traceroute_info": []
    }

    if not clean_host: 
        return failure_result

    try:
        ping_count = 5
        command = f"ping -c {ping_count} -W 1 {clean_host}"
        ping_output = subprocess.check_output(command, shell=True, text=True, stderr=subprocess.STDOUT)
        
        latencies = [float(t) for t in re.findall(r'time=([\d\.]+)', ping_output)]
        loss_match = re.search(r'(\d+)% packet loss', ping_output)
        packet_loss = float(loss_match.group(1)) if loss_match else 100.0

        if latencies:
            avg_ping = sum(latencies) / len(latencies)
            jitter = (sum((x - avg_ping) ** 2 for x in latencies) / (len(latencies) - 1)) ** 0.5 if len(latencies) > 1 else 0
            
            return {
                "name": target["name"], "ping": avg_ping, "jitter": jitter, "status": "UP",
                "packet_loss": packet_loss, "dns_info": [], "traceroute_info": []
            }
        else:
            return failure_result

    except subprocess.CalledProcessError:
         return failure_result
    except Exception as e:
        print(f"Error pinging {target['name']}: {e}")
        return failure_result

def continuous_ping_target(target, server_gateway_ip):
    """Continuously pings a target based on the update interval from settings."""
    while not stop_event.is_set():
        try:
            result = ping_target_once(target, server_gateway_ip)
            log_metric(result)
        except Exception as e:
            print(f"Unhandled error in continuous_ping_target for {target['name']}: {e}")
        
        # Use interval from global settings, which is updated periodically
        interval_ms = int(SETTINGS.get('updateInterval', 2000))
        interval_s = max(1.0, interval_ms / 1000.0) # Ensure at least 1s wait
        stop_event.wait(interval_s)

def get_settings_from_db():
    """Reads settings from the database and updates the global SETTINGS dictionary."""
    global SETTINGS
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            rows = cursor.fetchall()
            # Create a temporary dictionary to hold new settings
            new_settings = SETTINGS.copy()
            for row in rows:
                new_settings[row[0]] = row[1]
            # Atomically update the global settings
            SETTINGS = new_settings
    except Exception as e:
        print(f"Could not read settings from DB, using existing defaults. Error: {e}")

def monitor_settings():
    """Periodically checks the database for settings changes."""
    while not stop_event.is_set():
        get_settings_from_db()
        stop_event.wait(10) # Check for new settings every 10 seconds

def signal_handler(sig, frame):
    """Handles termination signals to shut down gracefully."""
    print("Signal received, shutting down.")
    stop_event.set()
    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError as e:
            print(f"Error removing socket file: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if os.path.exists(SOCKET_PATH):
        print(f"Warning: Stale socket file found at {SOCKET_PATH}. Removing.")
        os.remove(SOCKET_PATH)

    print("Starting A.N.U.S. Python Service...")
    setup_database()
    
    # --- Initial Startup Status Check ---
    print("Performing initial status check...")
    get_settings_from_db() # Get latest settings first
    server_gateway_ip = get_server_gateway_ip()
    print(f"Detected Server Gateway: {server_gateway_ip} on interface {NETWORK_INTERFACE}")
    
    targets = []
    if os.path.exists(TARGETS_CONFIG_FILE):
        try:
            with open(TARGETS_CONFIG_FILE, 'r') as f: 
                targets = json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not parse {TARGETS_CONFIG_FILE}. Using empty target list for startup check.")
    
    initial_metrics = []
    with ThreadPoolExecutor(max_workers=len(targets) or 1) as executor:
        future_to_target = {executor.submit(ping_target_once, target, server_gateway_ip): target for target in targets}
        for future in future_to_target:
            try:
                result = future.result()
                initial_metrics.append(result)
            except Exception as exc:
                print(f'{future_to_target[future]["name"]} generated an exception: {exc}')

    initial_status = calculate_internet_status(initial_metrics, SETTINGS)
    update_event_log(initial_status)
    print(f"Startup complete. Initial status '{initial_status}' has been logged.")
    # --- End of Initial Check ---

    # --- Start Continuous Monitoring Threads ---
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.submit(get_resource_usage)
        executor.submit(monitor_settings) # Thread to keep settings up-to-date
        
        # Start continuous pinging for all targets
        for target in targets:
            executor.submit(continuous_ping_target, target, server_gateway_ip)
    
    print("A.N.U.S. Service has shut down.")