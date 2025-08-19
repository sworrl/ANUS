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
import socket
import xml.etree.ElementTree as ET

# --- Configuration ---
DB_PATH = '/var/db/anus_metrics.db'
CLIENT_IP_FILE = '/var/db/anus_client_ip.txt'
TARGETS_CONFIG_FILE = '/var/www/html/anus/assets/targets.json'
FUZZY_SAYINGS_FILE = '/var/www/html/anus/assets/fuzzy_sayings.json'
NET_STATS_CACHE = '/var/tmp/anus_net_stats.json'
SOCKET_PATH = '/var/run/anus_service_cmd.sock'
try:
    output = subprocess.check_output("ip route | grep default | awk '{print $5}' | head -n 1", shell=True, text=True).strip()
    NETWORK_INTERFACE = output.splitlines()[0]
except Exception:
    NETWORK_INTERFACE = 'eth0'

DEFAULT_TARGETS = [
    {"name": "Gateway", "url": "DETECT_GATEWAY", "critical": True},
    {"name": "OpenDNS Primary", "url": "208.67.222.222", "critical": True},
    {"name": "Google DNS", "url": "8.8.8.8", "critical": True},
    {"name": "Cloudflare DNS", "url": "1.1.1.1", "critical": True},
    {"name": "NextDNS", "url": "dns.nextdns.io", "critical": True},
    {"name": "Google.com", "url": "google.com"},
    {"name": "Cloudflare.com", "url": "cloudflare.com"}
]
# FIX: Increased max workers
MAX_WORKERS = 100
stop_event = threading.Event()
GATEWAY_DETAILS = {'ip': 'N/A', 'mac': 'N/A', 'vendor': 'N/A'}

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS local_network_info (
                id INTEGER PRIMARY KEY, gateway_ip TEXT UNIQUE, gateway_mac TEXT,
                gateway_vendor TEXT, last_updated INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nmap_scan_results (
                id INTEGER PRIMARY KEY, ip TEXT UNIQUE, mac_address TEXT,
                vendor TEXT, hostname TEXT, is_up BOOLEAN,
                services TEXT, os TEXT, last_scanned INTEGER
            )
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS client_pings (ip TEXT PRIMARY KEY, ping REAL, timestamp INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
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

def update_diagnostics(name, dns_info, traceroute_info):
    """Updates the most recent metric record with new diagnostic info."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE metrics
            SET dns_info = ?, traceroute_info = ?
            WHERE id = (SELECT id FROM metrics WHERE name = ? ORDER BY timestamp DESC LIMIT 1)
        """, (json.dumps(dns_info), json.dumps(traceroute_info), name))
        conn.commit()

# --- System & Network Diagnostics ---
def get_resource_usage():
    while not stop_event.is_set():
        try:
            load_avg = os.getloadavg()[0]
            cpu_count = os.cpu_count()
            cpu_usage = (load_avg / cpu_count) * 100 if cpu_count else 0
            mem_info = subprocess.check_output("free -m", shell=True, text=True).splitlines()[1].split()
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
        time.sleep(5)

def get_network_details():
    """Fetches gateway IP, MAC, and vendor, with database caching."""
    global GATEWAY_DETAILS
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT gateway_ip, gateway_mac, gateway_vendor FROM local_network_info LIMIT 1")
        db_details = cursor.fetchone()

        current_gateway_ip = get_server_gateway_ip()
        
        if db_details and db_details[0] == current_gateway_ip and db_details[1] != 'N/A':
            print("Gateway details found in database, using cached data.")
            GATEWAY_DETAILS = {
                'ip': db_details[0],
                'mac': db_details[1],
                'vendor': db_details[2]
            }
        else:
            print("Gateway details not found or changed, fetching new data...")
            current_gateway_mac = 'N/A'
            current_gateway_vendor = 'N/A'

            if current_gateway_ip:
                arp_output = subprocess.check_output(f"ip neigh show {current_gateway_ip}", shell=True, text=True, stderr=subprocess.DEVNULL)
                match = re.search(r'lladdr ([0-9a-f:]+)', arp_output)
                if match:
                    current_gateway_mac = match.group(1)
                    try:
                        vendor_output = subprocess.check_output(f"curl -s https://api.macvendors.com/{current_gateway_mac}", shell=True, text=True, timeout=10)
                        if vendor_output and "Not Found" not in vendor_output:
                            current_gateway_vendor = vendor_output.strip()
                        else:
                            current_gateway_vendor = "Unknown Vendor"
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        current_gateway_vendor = "Vendor lookup failed"
            
            GATEWAY_DETAILS = {
                'ip': current_gateway_ip,
                'mac': current_gateway_mac,
                'vendor': current_gateway_vendor
            }

            cursor.execute("REPLACE INTO local_network_info (id, gateway_ip, gateway_mac, gateway_vendor, last_updated) VALUES (?, ?, ?, ?, ?)",
                           (1, GATEWAY_DETAILS['ip'], GATEWAY_DETAILS['mac'], GATEWAY_DETAILS['vendor'], int(time.time())))
            conn.commit()
    
    return GATEWAY_DETAILS

def get_server_gateway_ip():
    try:
        output = subprocess.check_output("ip route | grep default | awk '{print $3}' | head -n 1", shell=True, text=True).strip()
        return output.splitlines()[0]
    except Exception: return None

def get_network_cidr():
    """Determines the local network CIDR (e.g., 192.168.1.0/24)."""
    try:
        ip_output = subprocess.check_output(f"ip addr show {NETWORK_INTERFACE} | grep 'inet ' | awk '{{print $2}}'", shell=True, text=True).strip()
        return ip_output.splitlines()[0]
    except Exception:
        return '192.168.1.0/24'

def scan_network():
    """Performs an nmap scan and updates the database."""
    while not stop_event.is_set():
        try:
            network_range = get_network_cidr()
            print(f"Starting nmap scan of {network_range}...")
            # FIX: Try privileged scan first, fall back to TCP connect scan on error
            nmap_command = f"nmap -sS -sV -O -oX - {network_range}"
            try:
                nmap_output = subprocess.check_output(nmap_command, shell=True, text=True, stderr=subprocess.PIPE)
                if "requires root privileges" in nmap_output.lower() or "permission denied" in nmap_output.lower():
                    raise subprocess.CalledProcessError(1, nmap_command, stderr="Requires root privileges")
            except subprocess.CalledProcessError as e:
                print(f"Privileged nmap scan failed: {e.stderr.strip()}. Falling back to TCP connect scan.")
                nmap_command = f"nmap -sT -sV -O -oX - {network_range}"
                nmap_output = subprocess.check_output(nmap_command, shell=True, text=True)

            root = ET.fromstring(nmap_output)
            
            scanned_hosts = []
            for host in root.findall('host'):
                ip_element = host.find('address[@addrtype="ipv4"]')
                mac_element = host.find('address[@addrtype="mac"]')
                hostname_element = host.find('hostnames/hostname')
                status_element = host.find('status')
                os_element = host.find('os/osmatch')

                ip = ip_element.get('addr') if ip_element is not None else 'N/A'
                mac = mac_element.get('addr') if mac_element is not None else 'N/A'
                vendor = mac_element.get('vendor') if mac_element is not None else 'N/A'
                hostname = hostname_element.get('name') if hostname_element is not None else 'N/A'
                is_up = status_element.get('state') == 'up' if status_element is not None else False
                os_name = os_element.get('name') if os_element is not None else 'N/A'

                services = []
                for port_element in host.findall('ports/port'):
                    if port_element.find('state') is not None and port_element.find('state').get('state') == 'open':
                        service_element = port_element.find('service')
                        if service_element is not None:
                             services.append({
                                'port': port_element.get('portid'),
                                'protocol': port_element.get('protocol'),
                                'name': service_element.get('name')
                            })

                host_info = {
                    'ip': ip,
                    'mac_address': mac,
                    'vendor': vendor,
                    'hostname': hostname,
                    'is_up': is_up,
                    'os': os_name,
                    'services': services
                }
                scanned_hosts.append(host_info)
            
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM nmap_scan_results")
                for host in scanned_hosts:
                    services_json = json.dumps(host['services'])
                    cursor.execute("""
                        INSERT INTO nmap_scan_results (ip, mac_address, vendor, hostname, is_up, services, os, last_scanned)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        host['ip'], host['mac_address'], host['vendor'], host['hostname'],
                        host['is_up'], services_json, host['os'], int(time.time())
                    ))
                conn.commit()
            print("Nmap scan complete and database updated.")
        
        except Exception as e:
            print(f"Error in scan_network: {e}")
        
        stop_event.wait(300)

def get_diagnostics(target, server_gateway_ip):
    """Periodically runs traceroute and DNS lookups for a target."""
    is_gateway = target.get('url') == 'DETECT_GATEWAY'
    host_url = server_gateway_ip if is_gateway else target['url']
    clean_host = host_url.replace("https://", "").replace("http://", "").split('/')[0] if host_url else None
    if not clean_host: return

    while not stop_event.is_set():
        try:
            dns_info, traceroute_info = [], []
            try:
                dig_output = subprocess.check_output(f"dig +short {clean_host}", shell=True, timeout=10, text=True, stderr=subprocess.PIPE)
                dns_info = [line for line in dig_output.strip().split('\n') if line]
                if not dns_info:
                    dns_info = ["DNS lookup returned no records."]
            except Exception as e:
                dns_info = [f"DNS lookup failed: {e.stderr.strip() if e.stderr else str(e)}"]
            
            try:
                traceroute_output = subprocess.check_output(f"traceroute -n -q 1 -w 1 -m 15 {clean_host}", shell=True, timeout=30, text=True, stderr=subprocess.PIPE)
                traceroute_info = [line for line in traceroute_output.strip().split('\n') if line]
                if not traceroute_info:
                    traceroute_info = ["Traceroute returned no hops."]
            except Exception as e:
                traceroute_info = [f"Traceroute failed: {e.stderr.strip() if e.stderr else str(e)}"]
            
            update_diagnostics(target['name'], dns_info, traceroute_info)
        except Exception as e:
            print(f"Error in get_diagnostics for {target['name']}: {e}")
        
        stop_event.wait(300)

def continuous_ping_target(target, server_gateway_ip):
    while not stop_event.is_set():
        try:
            is_gateway = target.get('url') == 'DETECT_GATEWAY'
            host_url = server_gateway_ip if is_gateway else target['url']
            clean_host = host_url.replace("https://", "").replace("http://", "").split('/')[0] if host_url else None
            if not clean_host: 
                time.sleep(5)
                continue

            command = f"ping -i 0.2 -W 0.8 {clean_host}"
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
                elif "no answer yet" in line or "Destination Host Unreachable" in line or "Name or service not known" in line:
                    packets_sent += 1
                    packets_lost += 1
                
                if time.time() - last_log_time >= 1:
                    if latencies:
                        avg_ping = sum(latencies) / len(latencies)
                        jitter = (sum((x - avg_ping) ** 2 for x in latencies) / (len(latencies) - 1)) ** 0.5 if len(latencies) > 1 else 0
                        packet_loss = (packets_lost / packets_sent) * 100 if packets_sent > 0 else 0
                        log_metric({"name": target["name"], "ping": avg_ping, "jitter": jitter, "status": "UP", "packet_loss": packet_loss})
                    else:
                        log_metric({"name": target["name"], "status": "DOWN", "packet_loss": 100.0})
                    
                    latencies, packets_sent, packets_lost = [], 0, 0
                    last_log_time = time.time()

            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait()
        except Exception as e:
            print(f"Error in continuous_ping_target for {target['name']}, restarting in 5s. Error: {e}")
            time.sleep(5)

def on_demand_diagnostic(payload):
    """Performs a single diagnostic test on a user-provided target."""
    target = payload.get('target')
    ping_count = payload.get('count', 4)
    ping_size = payload.get('size', 56)
    ping_msg = payload.get('message', '')

    if not target:
        return {"error": "Target not specified."}
    
    host = target.replace("https://", "").replace("http://", "").split('/')[0]

    try:
        ping_command = f"ping -c {ping_count} -s {ping_size} {host}"
        ping_output = subprocess.check_output(ping_command, shell=True, text=True, stderr=subprocess.STDOUT)
        
        dns_output = subprocess.check_output(f"dig +short {host}", shell=True, text=True, stderr=subprocess.STDOUT)
        dns_info = [line for line in dns_output.strip().split('\n') if line]
        
        traceroute_output = subprocess.check_output(f"traceroute -n -q 1 -w 1 -m 15 {host}", shell=True, text=True, stderr=subprocess.STDOUT)
        traceroute_info = [line for line in traceroute_output.strip().split('\n') if line]

        return {
            "status": "success",
            "ping": ping_output,
            "dns": dns_info,
            "traceroute": traceroute_info
        }
    except subprocess.CalledProcessError as e:
        return {"error": f"Command failed: {e.output}"}
    except Exception as e:
        return {"error": str(e)}

def command_server():
    """Starts a local Unix domain socket server to handle on-demand commands."""
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
    
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    
    print(f"Command server listening on {SOCKET_PATH}")
    
    while not stop_event.is_set():
        try:
            conn, _ = server.accept()
            data = conn.recv(4096)
            if data:
                payload = json.loads(data.decode('utf-8'))
                command = payload.get('command')
                result = None
                if command == 'on_demand_diagnostic':
                    result = on_demand_diagnostic(payload)
                
                if result:
                    conn.sendall(json.dumps(result).encode('utf-8'))
            conn.close()
        except socket.timeout:
            continue
        except Exception as e:
            print(f"Error in command server: {e}")
            
    server.close()

def signal_handler(sig, frame):
    print("Signal received, shutting down.")
    stop_event.set()
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

# --- Main Execution ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("Starting A.N.U.S. Python Service...")
    setup_database()
    get_network_details()
    server_gateway_ip = GATEWAY_DETAILS['ip']
    print(f"Detected Server Gateway: {server_gateway_ip} on interface {NETWORK_INTERFACE}")
    
    targets = DEFAULT_TARGETS
    if os.path.exists(TARGETS_CONFIG_FILE):
        try:
            with open(TARGETS_CONFIG_FILE, 'r') as f: targets = json.load(f)
        except (json.JSONDecodeError): pass
    
    # FIX: Use the MAX_WORKERS variable for the thread pool
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.submit(get_resource_usage)
        executor.submit(scan_network)
        executor.submit(command_server)
        for target in targets:
            executor.submit(continuous_ping_target, target, server_gateway_ip)
            executor.submit(get_diagnostics, target, server_gateway_ip)
    
    print("A.N.U.S. Service has shut down.")
