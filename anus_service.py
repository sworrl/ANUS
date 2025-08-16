#!/usr/bin/env python3

import subprocess
import os
import time
import json
import sqlite3
import re
from concurrent.futures import ThreadPoolExecutor
import threading
import signal

# --- Configuration ---
DB_PATH = '/var/db/anus_metrics.db'
CLIENT_IP_FILE = '/var/db/anus_client_ip.txt'
TARGETS_CONFIG_FILE = '/var/www/html/anus/targets.json'
NET_STATS_CACHE = '/var/tmp/anus_net_stats.json'

try:
    output = subprocess.check_output("ip route | grep default | awk '{print $5}' | head -n 1", shell=True).strip().decode('utf-8')
    NETWORK_INTERFACE = output.splitlines()[0]
except Exception:
    NETWORK_INTERFACE = 'eth0'

DEFAULT_TARGETS = [
    {"name": "Gateway", "url": "DETECT_GATEWAY", "critical": True},
    {"name": "OpenDNS", "url": "208.67.222.222", "critical": True},
    {"name": "Google DNS", "url": "8.8.8.8", "critical": True},
    {"name": "Cloudflare DNS", "url": "1.1.1.1", "critical": True},
    {"name": "NextDNS", "url": "dns.nextdns.io", "critical": True},
    {"name": "Google.com", "url": "google.com"},
    {"name": "Cloudflare.com", "url": "cloudflare.com"}
]
MAX_WORKERS = 20 # Increased for more concurrent pings
stop_event = threading.Event()

# --- Database Functions ---
def setup_database():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL,
                status TEXT, dns_info TEXT, traceroute_info TEXT,
                timestamp INTEGER, packet_loss REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics (name, timestamp DESC)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resource_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,
                cpu_usage REAL, mem_usage REAL, net_down_kbps REAL, net_up_kbps REAL
            )
        """)
        try:
            cursor.execute("ALTER TABLE metrics ADD COLUMN packet_loss REAL")
        except sqlite3.OperationalError: pass
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS client_pings (ip TEXT PRIMARY KEY, ping REAL, timestamp INTEGER)")
        conn.commit()

def log_metric(metric):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO metrics (name, ping, jitter, status, dns_info, traceroute_info, timestamp, packet_loss)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metric['name'], metric.get('ping'), metric.get('jitter'), metric['status'],
            json.dumps(metric.get('dns_info', [])), json.dumps(metric.get('traceroute_info', [])),
            int(time.time()), metric.get('packet_loss')
        ))
        conn.commit()

def log_resource_usage(usage_data):
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

# --- System & Network Diagnostics ---
def get_resource_usage():
    while not stop_event.is_set():
        try:
            load_avg = os.getloadavg()[0]
            cpu_count = os.cpu_count()
            cpu_usage = (load_avg / cpu_count) * 100 if cpu_count else 0
            mem_info = subprocess.check_output("free -m", shell=True).decode().splitlines()[1].split()
            mem_total, mem_used = int(mem_info[1]), int(mem_info[2])
            mem_usage = (mem_used / mem_total) * 100 if mem_total else 0
            
            rx_rate, tx_rate = 0, 0
            now = time.time()
            try:
                with open(f"/sys/class/net/{NETWORK_INTERFACE}/statistics/rx_bytes") as f: rx_bytes = int(f.read())
                with open(f"/sys/class/net/{NETWORK_INTERFACE}/statistics/tx_bytes") as f: tx_bytes = int(f.read())
                last_stats = {}
                if os.path.exists(NET_STATS_CACHE):
                    with open(NET_STATS_CACHE, 'r') as f: last_stats = json.load(f)
                if 'timestamp' in last_stats and (time_diff := now - last_stats['timestamp']) > 0:
                    rx_rate = ((rx_bytes - last_stats['rx']) * 8) / time_diff / 1024
                    tx_rate = ((tx_bytes - last_stats['tx']) * 8) / time_diff / 1024
                with open(NET_STATS_CACHE, 'w') as f: json.dump({'rx': rx_bytes, 'tx': tx_bytes, 'timestamp': now}, f)
            except (IOError, ValueError): pass

            log_resource_usage({
                'cpu': round(cpu_usage, 2), 'mem': round(mem_usage, 2),
                'net_down': round(rx_rate, 2), 'net_up': round(tx_rate, 2)
            })
        except Exception as e:
            print(f"Error in get_resource_usage: {e}")
        time.sleep(5) # Log resources every 5 seconds

def get_server_gateway_ip():
    try:
        output = subprocess.check_output("ip route | grep default | awk '{print $3}' | head -n 1", shell=True).strip().decode('utf-8')
        return output.splitlines()[0]
    except Exception: return None

def continuous_ping_target(target, server_gateway_ip):
    is_gateway = target.get('url') == 'DETECT_GATEWAY'
    host_url = server_gateway_ip if is_gateway else target['url']
    clean_host = host_url.replace("https://", "").replace("http://", "").split('/')[0] if host_url else None
    if not clean_host: return

    command = f"ping -i 0.2 -W 0.8 {clean_host}" # Reduced timeout to fit 1s interval
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=os.setsid)
    
    latencies = []
    last_log_time = time.time()
    packets_sent, packets_lost = 0, 0
    
    for line in iter(process.stdout.readline, ''):
        if stop_event.is_set(): break
        
        match_time = re.search(r'time=(\d+\.?\d*)', line)
        if match_time:
            packets_sent += 1
            latencies.append(float(match_time.group(1)))
        elif "no answer yet" in line or "Destination Host Unreachable" in line:
            packets_sent += 1
            packets_lost += 1
        
        if time.time() - last_log_time >= 1: # MODIFICATION: Log every 1 second
            if latencies:
                avg_ping = sum(latencies) / len(latencies)
                jitter = (sum((x - avg_ping) ** 2 for x in latencies) / (len(latencies) - 1)) ** 0.5 if len(latencies) > 1 else 0
                packet_loss = (packets_lost / packets_sent) * 100 if packets_sent > 0 else 0
                log_metric({
                    "name": target["name"], "ping": avg_ping, "jitter": jitter, 
                    "status": "UP", "packet_loss": packet_loss
                })
            else:
                log_metric({
                    "name": target["name"], "ping": None, "jitter": None, "status": "DOWN", "packet_loss": 100.0
                })
            
            latencies, packets_sent, packets_lost = [], 0, 0
            last_log_time = time.time()

    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    process.wait()

def signal_handler(sig, frame):
    print("Signal received, shutting down.")
    stop_event.set()

# --- Main Execution ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("Starting A.N.U.S. Python Service...")
    setup_database()
    
    server_gateway_ip = get_server_gateway_ip()
    print(f"Detected Server Gateway: {server_gateway_ip} on interface {NETWORK_INTERFACE}")
    
    targets = DEFAULT_TARGETS
    if not os.path.exists(TARGETS_CONFIG_FILE):
        with open(TARGETS_CONFIG_FILE, 'w') as f: json.dump(DEFAULT_TARGETS, f, indent=4)
    else:
        try:
            with open(TARGETS_CONFIG_FILE, 'r') as f: targets = json.load(f)
        except (json.JSONDecodeError): pass
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.submit(get_resource_usage)
        for target in targets:
            executor.submit(continuous_ping_target, target, server_gateway_ip)
    
    print("A.N.U.S. Service has shut down.")
