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
import ipaddress

# --- Configuration ---
DB_PATH = '/var/db/anus_metrics.db'
CLIENT_IP_FILE = '/var/db/anus_client_ip.txt'
TARGETS_CONFIG_FILE = '/var/www/html/anus/assets/targets.json'
NET_STATS_CACHE = '/var/tmp/anus_net_stats.json'
SOCKET_PATH = '/var/run/anus_service_cmd.sock'
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
    'criticalServices': 'Gateway,OpenDNS Primary,Google DNS,Cloudflare DNS'
}
MAX_WORKERS = 100
NETWORK_SCAN_LOCK = threading.Lock()

# --- Database Functions ---
def setup_database():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL, status TEXT, dns_info TEXT, traceroute_info TEXT, timestamp INTEGER, packet_loss REAL, is_on_demand BOOLEAN DEFAULT 0)")
        try:
            cursor.execute("ALTER TABLE metrics ADD COLUMN min_ping_15m REAL")
            cursor.execute("ALTER TABLE metrics ADD COLUMN max_ping_15m REAL")
            cursor.execute("ALTER TABLE metrics ADD COLUMN packet_loss_15m REAL")
            print("DB Migration: Added 15m stats columns.")
        except sqlite3.OperationalError:
            pass # Columns already exist
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics (name, timestamp DESC)")
        cursor.execute("CREATE TABLE IF NOT EXISTS resource_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL, cpu_usage REAL, mem_usage REAL, net_down_kbps REAL, net_up_kbps REAL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS local_network_info (id INTEGER PRIMARY KEY, gateway_ip TEXT UNIQUE, gateway_mac TEXT, gateway_vendor TEXT, last_updated INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS nmap_scan_results (id INTEGER PRIMARY KEY, ip TEXT UNIQUE, mac_address TEXT, vendor TEXT, hostname TEXT, is_up BOOLEAN, services TEXT, os TEXT, last_scanned INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, startTime INTEGER NOT NULL, endTime INTEGER)")
        conn.commit()

def log_metric(metric):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.cursor()
        now = int(time.time())
        time_15m_ago = now - 900
        cursor.execute("SELECT MIN(ping), MAX(ping) FROM metrics WHERE name = ? AND timestamp >= ?", (metric['name'], time_15m_ago))
        min_p, max_p = cursor.fetchone()
        
        min_ping_15m = min(p for p in [min_p, metric.get('ping')] if p is not None) if metric['status'] == 'UP' else min_p
        max_ping_15m = max(p for p in [max_p, metric.get('ping')] if p is not None) if metric['status'] == 'UP' else max_p
        
        cursor.execute("SELECT COUNT(*) FROM metrics WHERE name = ? AND timestamp >= ? AND status = 'DOWN'", (metric['name'], time_15m_ago))
        down_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM metrics WHERE name = ? AND timestamp >= ?", (metric['name'], time_15m_ago))
        total_count = cursor.fetchone()[0]
        # Add current result to the count for accuracy
        total_count += 1
        if metric.get('status') == 'DOWN':
            down_count += 1
        
        packet_loss_15m = (down_count / total_count) * 100 if total_count > 0 else 0
        
        cursor.execute("INSERT INTO metrics (name, ping, jitter, status, dns_info, traceroute_info, timestamp, packet_loss, is_on_demand, min_ping_15m, max_ping_15m, packet_loss_15m) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (metric['name'], metric.get('ping'), metric.get('jitter'), metric['status'], json.dumps(metric.get('dns_info', [])), json.dumps(metric.get('traceroute_info', [])), now, metric.get('packet_loss'), metric.get('is_on_demand', 0), min_ping_15m, max_ping_15m, round(packet_loss_15m,2)))
        conn.commit()

def log_resource_usage(usage_data):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO resource_metrics (timestamp, cpu_usage, mem_usage, net_down_kbps, net_up_kbps) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), usage_data['cpu'], usage_data['mem'], usage_data['net_down'], usage_data['net_up']))
        conn.commit()

def update_event_log(current_status):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM event_log ORDER BY startTime DESC LIMIT 1")
        last_event = cursor.fetchone()

        if not last_event or last_event[1] in ['Monitoring Started', 'Stats Reset']:
             print(f"Logging first status after start/reset: {current_status}")
             if last_event: # End the placeholder event
                 cursor.execute("UPDATE event_log SET endTime = ? WHERE id = ?", (int(time.time()), last_event[0]))
             cursor.execute("INSERT INTO event_log (status, startTime) VALUES (?, ?)", (current_status, int(time.time())))
        elif last_event[1] != current_status:
            print(f"Status changed from {last_event[1]} to {current_status}. Logging event.")
            cursor.execute("UPDATE event_log SET endTime = ? WHERE id = ?", (int(time.time()), last_event[0]))
            cursor.execute("INSERT INTO event_log (status, startTime) VALUES (?, ?)", (current_status, int(time.time())))
        conn.commit()

# --- Status & Diagnostics ---
def calculate_internet_status(latest_metrics, settings):
    method = settings.get('onlineDetectionMethod', 'smart_check')
    critical_services = [s.strip() for s in settings.get('criticalServices', '').split(',') if s.strip()]
    up_count, down_count, critical_up_count, critical_down_count, gateway_status = 0, 0, 0, 0, 'DOWN'

    for metric in latest_metrics:
        is_critical = metric['name'] in critical_services
        if metric['status'] == 'UP': up_count += 1; critical_up_count += 1 if is_critical else 0
        else: down_count += 1; critical_down_count += 1 if is_critical else 0
        if metric['name'] == 'Gateway': gateway_status = metric['status']

    if method == 'gateway': return gateway_status
    if method == 'majority': return 'UP' if up_count > 0 and up_count >= down_count else 'DOWN'
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
                last_stats = {}
                if os.path.exists(NET_STATS_CACHE):
                    with open(NET_STATS_CACHE, 'r') as f: last_stats = json.load(f)
                if 'timestamp' in last_stats and (time_diff := now - last_stats['timestamp']) > 0:
                    rx_rate = ((rx_bytes - last_stats['rx']) * 8) / time_diff / 1024; tx_rate = ((tx_bytes - last_stats['tx']) * 8) / time_diff / 1024
                with open(NET_STATS_CACHE, 'w') as f: json.dump({'rx': rx_bytes, 'tx': tx_bytes, 'timestamp': now}, f)
            except (IOError, ValueError, FileNotFoundError): pass
            log_resource_usage({'cpu': round(cpu_usage, 2), 'mem': round(mem_usage, 2), 'net_down': round(rx_rate, 2), 'net_up': round(tx_rate, 2)})
        except Exception as e: print(f"Error in get_resource_usage: {e}")
        stop_event.wait(5)

def get_server_gateway_ip():
    try: return subprocess.check_output("ip route | grep default | awk '{print $3}' | head -n 1", shell=True, text=True).strip()
    except Exception: return None

def get_local_subnet():
    try:
        output = subprocess.check_output(f"ip -o -f inet addr show {NETWORK_INTERFACE}", shell=True, text=True)
        match = re.search(r'inet\s+([\d\.]+/\d+)', output)
        if match:
            ip_interface = ipaddress.ip_interface(match.group(1))
            return str(ip_interface.network)
    except (subprocess.CalledProcessError, ValueError, IndexError) as e:
        print(f"Could not determine local subnet: {e}")
        return None

def scan_network_neighborhood(is_manual_scan=False):
    if not NETWORK_SCAN_LOCK.acquire(blocking=False):
        print("Network scan already in progress. Skipping.")
        return "Scan already in progress."

    try:
        print("Starting network neighborhood scan...")
        subnet = get_local_subnet()
        if not subnet:
            print("Cannot scan neighborhood, subnet not found.")
            return "Could not determine local subnet."
        
        command = f"sudo nmap -oX - -sn {subnet}"
        nmap_output = subprocess.check_output(command, shell=True, text=True, stderr=subprocess.DEVNULL)
        root = ET.fromstring(nmap_output)
        
        found_ips = set()
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            now = int(time.time())
            for host in root.findall('host'):
                ip_addr = host.find("./address[@addrtype='ipv4']").get('addr')
                mac_addr_elem = host.find("./address[@addrtype='mac']")
                mac_addr = mac_addr_elem.get('addr') if mac_addr_elem is not None else 'N/A'
                vendor = mac_addr_elem.get('vendor') if mac_addr_elem is not None else 'N/A'
                hostname = host.find("./hostnames/hostname")
                hostname = hostname.get('name') if hostname is not None else 'N/A'
                
                found_ips.add(ip_addr)
                
                cursor.execute("""
                    INSERT INTO nmap_scan_results (ip, mac_address, vendor, hostname, is_up, last_scanned)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ip) DO UPDATE SET
                        mac_address=excluded.mac_address,
                        vendor=excluded.vendor,
                        hostname=excluded.hostname,
                        is_up=excluded.is_up,
                        last_scanned=excluded.last_scanned
                """, (ip_addr, mac_addr, vendor, hostname, True, now))
            
            # Mark hosts that were not found as down
            cursor.execute("UPDATE nmap_scan_results SET is_up = 0 WHERE ip NOT IN ({})".format(','.join('?' for _ in found_ips)), tuple(found_ips))
            conn.commit()
        print(f"Network scan complete. Found {len(found_ips)} devices.")
        return f"Scan complete. Found {len(found_ips)} devices."

    except subprocess.CalledProcessError:
        print("Nmap scan command failed. Is nmap installed and sudo permissions correct?")
        return "Nmap command failed."
    except ET.ParseError:
        print("Failed to parse nmap XML output.")
        return "Failed to parse nmap output."
    except Exception as e:
        print(f"An unexpected error occurred during network scan: {e}")
        return f"An unexpected error occurred: {e}"
    finally:
        NETWORK_SCAN_LOCK.release()

def periodic_network_scanner():
    while not stop_event.is_set():
        scan_network_neighborhood()
        stop_event.wait(900) # Scan every 15 minutes

def update_gateway_details():
    while not stop_event.is_set():
        gateway_ip = get_server_gateway_ip()
        if not gateway_ip:
            stop_event.wait(300)
            continue
        try:
             with sqlite3.connect(DB_PATH, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT mac_address, vendor FROM nmap_scan_results WHERE ip = ?", (gateway_ip,))
                result = cursor.fetchone()
                if result:
                    mac, vendor = result
                    cursor.execute("INSERT INTO local_network_info (gateway_ip, gateway_mac, gateway_vendor, last_updated) VALUES (?, ?, ?, ?) ON CONFLICT(gateway_ip) DO UPDATE SET gateway_mac=excluded.gateway_mac, gateway_vendor=excluded.gateway_vendor, last_updated=excluded.last_updated", (gateway_ip, mac, vendor, int(time.time())))
                    conn.commit()
        except Exception as e:
            print(f"Error updating gateway details from nmap results: {e}")
        stop_event.wait(60) # Update gateway details every minute from the main scan table

def ping_target_once(target, server_gateway_ip):
    host_url = server_gateway_ip if target.get('url') == 'DETECT_GATEWAY' else target['url']
    clean_host = host_url.replace("https://", "").replace("http://", "").split('/')[0] if host_url else None
    failure_result = {"name": target["name"], "status": "DOWN", "packet_loss": 100.0, "ping": None, "jitter": None, "dns_info": [], "traceroute_info": []}
    if not clean_host: return failure_result

    try:
        command = f"ping -c 5 -W 1 {clean_host}"
        ping_output = subprocess.check_output(command, shell=True, text=True, stderr=subprocess.STDOUT)
        latencies = [float(t) for t in re.findall(r'time=([\d\.]+)', ping_output)]
        loss_match = re.search(r'(\d+)% packet loss', ping_output)
        packet_loss = float(loss_match.group(1)) if loss_match else 100.0
        if latencies:
            avg_ping = sum(latencies) / len(latencies)
            jitter = (sum((x - avg_ping) ** 2 for x in latencies) / (len(latencies) - 1)) ** 0.5 if len(latencies) > 1 else 0
            return {"name": target["name"], "ping": avg_ping, "jitter": jitter, "status": "UP", "packet_loss": packet_loss, "dns_info": [], "traceroute_info": []}
        return failure_result
    except (subprocess.CalledProcessError, Exception): return failure_result

def continuous_ping_target(target, server_gateway_ip):
    while not stop_event.is_set():
        try: log_metric(ping_target_once(target, server_gateway_ip))
        except Exception as e: print(f"Error for {target['name']}: {e}")
        stop_event.wait(max(1.0, int(SETTINGS.get('updateInterval', 2000)) / 1000.0))

def get_settings_from_db():
    global SETTINGS
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT key, value FROM settings").fetchall()
            db_settings = {row[0]: row[1] for row in rows}
            SETTINGS.update(db_settings) # Update global dict with any new settings from DB
    except Exception as e: print(f"Could not read settings from DB. Error: {e}")

def monitor_settings():
    while not stop_event.is_set(): get_settings_from_db(); stop_event.wait(10)

def socket_listener(executor):
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(5)
    os.chmod(SOCKET_PATH, 0o777)
    print(f"Socket listener started at {SOCKET_PATH}")

    while not stop_event.is_set():
        try:
            conn, addr = server.accept()
            data_str = conn.recv(1024).decode()
            if data_str:
                command = json.loads(data_str)
                action = command.get('action')
                response = {}
                if action == 'trigger_network_scan':
                    print("Manual network scan triggered via socket.")
                    executor.submit(scan_network_neighborhood, is_manual_scan=True)
                    response = {"status": "success", "message": "Network scan initiated."}
                else:
                    response = {"status": "error", "message": "Unknown action"}
                
                conn.sendall(json.dumps(response).encode())
            conn.close()
        except Exception as e:
            if not stop_event.is_set():
                print(f"Socket listener error: {e}")

def signal_handler(sig, frame):
    print("Signal received, shutting down."); stop_event.set()
    if os.path.exists(SOCKET_PATH):
        try: os.remove(SOCKET_PATH)
        except OSError as e: print(f"Error removing socket file: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    print("Starting A.N.U.S. Python Service v1.4.0..."); 
    setup_database(); 
    get_settings_from_db()
    
    server_gateway_ip = get_server_gateway_ip()
    print(f"Detected Server Gateway: {server_gateway_ip} on interface {NETWORK_INTERFACE}")
    targets = []
    if os.path.exists(TARGETS_CONFIG_FILE):
        try:
            with open(TARGETS_CONFIG_FILE, 'r') as f: targets = json.load(f)
        except json.JSONDecodeError: print(f"Warning: Could not parse {TARGETS_CONFIG_FILE}.")
    
    initial_metrics = [ping_target_once(t, server_gateway_ip) for t in targets]
    initial_status = calculate_internet_status(initial_metrics, SETTINGS)
    update_event_log(initial_status)
    print(f"Startup complete. Initial status '{initial_status}' has been logged.")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.submit(socket_listener, executor)
        executor.submit(get_resource_usage)
        executor.submit(monitor_settings)
        executor.submit(periodic_network_scanner) # For neighborhood
        executor.submit(update_gateway_details)  # For specific gateway info
        for target in targets: executor.submit(continuous_ping_target, target, server_gateway_ip)
    
    print("A.N.U.S. Service has shut down.")

