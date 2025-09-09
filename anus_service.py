#!/usr/bin/env python3
# A.N.U.S. v1.4.0 - Service Update

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
DB_TIMEOUT = 10 # Seconds to wait for the database to be available

try:
    output = subprocess.check_output("ip route | grep default | awk '{print $5}' | head -n 1", shell=True, text=True).strip()
    NETWORK_INTERFACE = output.splitlines()[0]
except Exception:
    NETWORK_INTERFACE = 'eth0' 

# --- Global State ---
stop_event = threading.Event()
SETTINGS = {
    'updateInterval': 2000,
    'onlineDetectionMethod': 'smart_check',
    'smartCheckThreshold': 3,
    'criticalServices': 'Gateway,NextDNS Primary,Google DNS,Cloudflare DNS'
}
MAX_WORKERS = 100

# --- Database Functions ---
def setup_database():
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL, status TEXT, dns_info TEXT, traceroute_info TEXT, timestamp INTEGER, packet_loss REAL, is_on_demand BOOLEAN DEFAULT 0, min_ping_15m REAL, max_ping_15m REAL, packet_loss_15m REAL)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics (name, timestamp DESC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS resource_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL, cpu_usage REAL, mem_usage REAL, net_down_kbps REAL, net_up_kbps REAL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS local_network_info (id INTEGER PRIMARY KEY, gateway_ip TEXT UNIQUE, last_updated INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS nmap_scan_results (id INTEGER PRIMARY KEY, ip TEXT UNIQUE, mac_address TEXT, vendor TEXT, hostname TEXT, is_up BOOLEAN, services TEXT, os TEXT, last_scanned INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, startTime INTEGER NOT NULL, endTime INTEGER)")
        conn.commit()

def log_metric(metric):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
        cursor = conn.cursor()
        now = int(time.time())
        time_15m_ago = now - 900
        cursor.execute("SELECT MIN(ping), MAX(ping) FROM metrics WHERE name = ? AND timestamp >= ? AND ping IS NOT NULL", (metric['name'], time_15m_ago))
        min_p, max_p = cursor.fetchone()
        
        current_ping = metric.get('ping')
        min_ping_15m = min(p for p in [min_p, current_ping] if p is not None) if current_ping is not None else min_p
        max_ping_15m = max(p for p in [max_p, current_ping] if p is not None) if current_ping is not None else max_p
        
        cursor.execute("SELECT COUNT(*) FROM metrics WHERE name = ? AND timestamp >= ? AND status = 'DOWN'", (metric['name'], time_15m_ago))
        down_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM metrics WHERE name = ? AND timestamp >= ?", (metric['name'], time_15m_ago))
        total_count = cursor.fetchone()[0]
        packet_loss_15m = (down_count / (total_count + 1)) * 100 if (total_count + 1) > 0 else 0
        
        cursor.execute("INSERT INTO metrics (name, ping, jitter, status, dns_info, traceroute_info, timestamp, packet_loss, is_on_demand, min_ping_15m, max_ping_15m, packet_loss_15m) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (metric['name'], metric.get('ping'), metric.get('jitter'), metric['status'], json.dumps(metric.get('dns_info', [])), json.dumps(metric.get('traceroute_info', [])), now, metric.get('packet_loss'), metric.get('is_on_demand', 0), min_ping_15m, max_ping_15m, round(packet_loss_15m,2)))
        conn.commit()

def log_resource_usage(usage_data):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO resource_metrics (timestamp, cpu_usage, mem_usage, net_down_kbps, net_up_kbps) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), usage_data['cpu'], usage_data['mem'], usage_data['net_down'], usage_data['net_up']))
        conn.commit()

def log_system_event(event_status):
    now = int(time.time())
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE event_log SET endTime = ? WHERE endTime IS NULL", (now,))
        cursor.execute("INSERT INTO event_log (status, startTime, endTime) VALUES (?, ?, ?)", (event_status, now, now))
        conn.commit()
        print(f"Logged system event: {event_status}")

def update_event_log(current_status):
    with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM event_log ORDER BY startTime DESC LIMIT 1")
        last_event = cursor.fetchone()
        now = int(time.time())

        if not last_event or last_event[1] != current_status:
            if last_event:
                cursor.execute("UPDATE event_log SET endTime = ? WHERE id = ?", (now, last_event[0]))
            cursor.execute("INSERT INTO event_log (status, startTime) VALUES (?, ?)", (current_status, now))
            print(f"Status changed to {current_status}. Logging event.")
        conn.commit()

def calculate_internet_status(latest_metrics, settings):
    if not latest_metrics: return "Awaiting Data"
    method = settings.get('onlineDetectionMethod', 'smart_check')
    critical_services = [s.strip() for s in settings.get('criticalServices', '').split(',') if s.strip()]
    up_count, down_count, critical_up_count, critical_down_count, gateway_status = 0, 0, 0, 0, 'DOWN'
    for metric in latest_metrics:
        is_critical = metric['name'] in critical_services
        if metric['status'] == 'UP': up_count += 1; critical_up_count += 1 if is_critical else 0
        else: down_count += 1; critical_down_count += 1 if is_critical else 0
        if metric['name'] == 'Gateway': gateway_status = metric['status']
    if method == 'gateway': return gateway_status
    if method == 'majority': return 'UP' if up_count >= down_count else 'DOWN'
    if method == 'critical_services': return 'UP' if critical_down_count == 0 and critical_services else 'DOWN'
    if gateway_status == 'DOWN': return 'DOWN'
    return 'DOWN' if critical_down_count >= int(settings.get('smartCheckThreshold', 3)) else 'UP'

def get_resource_usage():
    while not stop_event.is_set():
        try:
            load_avg, cpu_count = os.getloadavg()[0], os.cpu_count() or 1; cpu_usage = (load_avg / cpu_count) * 100
            mem_info = subprocess.check_output("free -m", shell=True, text=True).splitlines()[1].split(); mem_total, mem_used = int(mem_info[1]), int(mem_info[2]); mem_usage = (mem_used / mem_total) * 100 if mem_total else 0
            rx_rate, tx_rate, now = 0, 0, time.time()
            try:
                with open(f"/sys/class/net/{NETWORK_INTERFACE}/statistics/rx_bytes") as f: rx_bytes = int(f.read())
                with open(f"/sys/class/net/{NETWORK_INTERFACE}/statistics/tx_bytes") as f: tx_bytes = int(f.read())
                last_stats = json.load(open(NET_STATS_CACHE, 'r')) if os.path.exists(NET_STATS_CACHE) else {}
                if 'timestamp' in last_stats and (time_diff := now - last_stats['timestamp']) > 0:
                    rx_rate = ((rx_bytes - last_stats['rx']) * 8) / time_diff / 1024; tx_rate = ((tx_bytes - last_stats['tx']) * 8) / time_diff / 1024
                with open(NET_STATS_CACHE, 'w') as f: json.dump({'rx': rx_bytes, 'tx': tx_bytes, 'timestamp': now}, f)
            except (IOError, ValueError, FileNotFoundError): pass
            log_resource_usage({'cpu': round(cpu_usage, 2), 'mem': round(mem_usage, 2), 'net_down': round(rx_rate, 2), 'net_up': round(tx_rate, 2)})
        except Exception as e: print(f"Error in get_resource_usage: {e}")
        stop_event.wait(5)

def get_server_gateway_ip():
    try: 
        ip = subprocess.check_output("ip route | grep default | awk '{print $3}' | head -n 1", shell=True, text=True).strip()
        if ip:
            with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
                conn.execute("INSERT OR REPLACE INTO local_network_info (id, gateway_ip, last_updated) VALUES (1, ?, ?)", (ip, int(time.time())))
                conn.commit()
            return ip
    except Exception: return None

def get_local_subnet():
    try:
        output = subprocess.check_output(f"ip -o -f inet addr show {NETWORK_INTERFACE} | awk '/scope global/ {{print $4}}'", shell=True, text=True).strip()
        return output
    except Exception:
        return None

def scan_network_neighborhood():
    while not stop_event.is_set():
        subnet = get_local_subnet()
        if not subnet:
            print("Could not determine local subnet. Skipping network scan.")
            stop_event.wait(900); continue
        print(f"Starting network scan for subnet: {subnet}...")
        try:
            command = f"sudo nmap -oX - -sn {subnet}"
            nmap_output = subprocess.check_output(command, shell=True, text=True, stderr=subprocess.DEVNULL)
            root = ET.fromstring(nmap_output)
            hosts = []
            for host_element in root.findall('host'):
                ip_addr, mac_addr, vendor, hostname = 'N/A', 'N/A', 'N/A', 'N/A'
                if host_element.find("./address[@addrtype='ipv4']") is not None: ip_addr = host_element.find("./address[@addrtype='ipv4']").get('addr')
                if host_element.find("./address[@addrtype='mac']") is not None:
                    mac_addr = host_element.find("./address[@addrtype='mac']").get('addr')
                    vendor = host_element.find("./address[@addrtype='mac']").get('vendor', 'N/A')
                if host_element.find("./hostnames/hostname") is not None: hostname = host_element.find("./hostnames/hostname").get('name')
                hosts.append((ip_addr, mac_addr, vendor, hostname, 1, '[]', 'N/A', int(time.time())))
            
            with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE nmap_scan_results SET is_up = 0")
                cursor.executemany("INSERT INTO nmap_scan_results (ip, mac_address, vendor, hostname, is_up, services, os, last_scanned) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ip) DO UPDATE SET mac_address=excluded.mac_address, vendor=excluded.vendor, hostname=excluded.hostname, is_up=excluded.is_up, last_scanned=excluded.last_scanned", hosts)
                conn.commit()
            print("Network scan complete. Database updated.")
        except (subprocess.CalledProcessError, ET.ParseError) as e: print(f"Nmap scan failed. Error: {e}")
        except Exception as e: print(f"Unexpected error during network scan: {e}")
        stop_event.wait(900)

def ping_target_once(target, server_gateway_ip):
    host_url = server_gateway_ip if target.get('url') == 'DETECT_GATEWAY' else target['url']
    clean_host = host_url.replace("https://", "").replace("http://", "").split('/')[0] if host_url else None
    failure_result = {"name": target["name"], "status": "DOWN", "packet_loss": 100.0, "ping": None, "jitter": None, "dns_info": [], "traceroute_info": []}
    if not clean_host: return failure_result
    try:
        command = f"ping -c 5 -W 1 {clean_host}"
        ping_output = subprocess.check_output(command, shell=True, text=True, stderr=subprocess.STDOUT)
        latencies = [float(t) for t in re.findall(r'time=([\d\.]+)', ping_output)]
        loss = float(re.search(r'(\d+)% packet loss', ping_output).group(1)) if re.search(r'(\d+)% packet loss', ping_output) else 100.0
        if latencies:
            avg_ping = sum(latencies) / len(latencies)
            jitter = (sum((x - avg_ping) ** 2 for x in latencies) / (len(latencies) - 1)) ** 0.5 if len(latencies) > 1 else 0
            dns_info, traceroute_info = [], []
            try:
                dns_output = subprocess.check_output(f"dig +short {clean_host}", shell=True, text=True, timeout=5)
                dns_info = [line for line in dns_output.strip().split('\n') if line]
            except Exception: pass
            try:
                trace_output = subprocess.check_output(f"traceroute -q 1 -w 1 -n {clean_host}", shell=True, text=True, timeout=15)
                traceroute_info = [line for line in trace_output.strip().split('\n') if line]
            except Exception: pass
            return {"name": target["name"], "ping": avg_ping, "jitter": jitter, "status": "UP", "packet_loss": loss, "dns_info": dns_info, "traceroute_info": traceroute_info}
        return failure_result
    except Exception: return failure_result

def continuous_ping_target(target, server_gateway_ip):
    while not stop_event.is_set():
        try: log_metric(ping_target_once(target, server_gateway_ip))
        except Exception as e: print(f"Error for {target['name']}: {e}")
        stop_event.wait(max(1.0, int(SETTINGS.get('updateInterval', 2000)) / 1000.0))

def get_settings_from_db():
    global SETTINGS
    try:
        with sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT) as conn:
            rows = conn.cursor().execute("SELECT key, value FROM settings").fetchall()
            SETTINGS.update({row[0]: row[1] for row in rows})
    except Exception as e: print(f"Could not read settings from DB. Error: {e}")

def monitor_settings():
    while not stop_event.is_set(): get_settings_from_db(); stop_event.wait(10)

def signal_handler(sig, frame):
    print("Signal received, shutting down.");
    log_system_event("Service Shutdown")
    stop_event.set()
    if os.path.exists(SOCKET_PATH):
        try: os.remove(SOCKET_PATH)
        except OSError as e: print(f"Error removing socket file: {e}")

def log_startup_event():
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            if uptime_seconds < 300: # Less than 5 minutes
                log_system_event("System Rebooted")
            else:
                log_system_event("Service Started")
    except Exception as e:
        print(f"Could not determine uptime, logging generic start event. Error: {e}")
        log_system_event("Service Started")


# --- Main Execution ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    if os.path.exists(SOCKET_PATH): os.remove(SOCKET_PATH)
    print("Starting A.N.U.S. Python Service v1.4.0..."); 
    setup_database()
    log_startup_event()
    get_settings_from_db()
    
    server_gateway_ip = get_server_gateway_ip()
    print(f"Detected Server Gateway: {server_gateway_ip} on interface {NETWORK_INTERFACE}")
    targets = json.load(open(TARGETS_CONFIG_FILE, 'r')) if os.path.exists(TARGETS_CONFIG_FILE) else []
    
    initial_metrics = [ping_target_once(t, server_gateway_ip) for t in targets]
    initial_status = calculate_internet_status(initial_metrics, SETTINGS)
    update_event_log(initial_status)
    print(f"Startup complete. Initial status '{initial_status}' has been logged.")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.submit(get_resource_usage)
        executor.submit(monitor_settings)
        executor.submit(scan_network_neighborhood)
        for target in targets: executor.submit(continuous_ping_target, target, server_gateway_ip)
    
    print("A.N.U.S. Service has shut down.")

