#!/usr/bin/python3
# A.N.U.S. v1.3.9

import subprocess
import os
import sys
import sqlite3
import time
import socket
import shutil
import re
from datetime import datetime
import json
import threading

# --- Curses Check ---
try:
    import curses
    import textwrap
    CURSES_AVAILABLE = True
except ImportError:
    CURSES_AVAILABLE = False

# --- Configuration ---
APP_DIR = "/var/www/html/anus"
ASSETS_DIR = os.path.join(APP_DIR, "assets")
APACHE_CONF_PATH = "/etc/apache2/sites-available/anus.conf"
DB_DIR = "/var/db"
DB_FILE = os.path.join(DB_DIR, "anus_metrics.db")
CLIENT_IP_FILE = os.path.join(DB_DIR, "anus_client_ip.txt")
SERVICE_NAME = "anus_service"
PYTHON_SERVICE_FILE = "anus_service.py"
SYSTEMD_SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}.service"
SUDOERS_FILE = "/etc/sudoers.d/anus-permissions"
VERSION = "v1.3.9"
SSL_INFO_CONFIG_FILE = os.path.join(ASSETS_DIR, "ssl_info.json")
VERBOSE_MODE = any(arg in ['-verbose', '--verbose', '/verbose'] for arg in sys.argv)

# --- Assets to be self-hosted ---
ASSETS = {
    "tailwindcss.js": "https://cdn.tailwindcss.com",
    "tone.js": "https://cdnjs.cloudflare.com/ajax/libs/tone/14.7.77/Tone.js",
    "chart.js": "https://cdn.jsdelivr.net/npm/chart.js",
    "chartjs-adapter-date-fns.bundle.min.js": "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js",
    "hammer.min.js": "https://cdnjs.cloudflare.com/ajax/libs/hammer.js/2.0.8/hammer.min.js",
    "chartjs-plugin-zoom.min.js": "https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-zoom/2.0.1/chartjs-plugin-zoom.min.js",
    "inter.css": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
}

# --- Color Codes for Simple Output ---
class colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

# --- Simple Text-Based Functions ---
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def simple_print_header(message):
    print(f"\n{colors.BOLD}{colors.HEADER}--- {message} ---{colors.ENDC}")

def simple_print_success(message):
    print(f"{colors.GREEN}✔ {message}{colors.ENDC}")

def simple_print_warning(message):
    print(f"{colors.YELLOW}⚠ {message}{colors.ENDC}", file=sys.stderr)

def simple_print_error(message):
    print(f"{colors.RED}✖ {message}{colors.ENDC}", file=sys.stderr)

def simple_print_info(message):
    print(f"{colors.BLUE}{message}{colors.ENDC}")

def simple_run_command(command, ignore_errors=False, show_output=True):
    if VERBOSE_MODE:
        simple_print_info(f"Executing: {command}")
    try:
        if show_output:
            process = subprocess.run(command, shell=True, check=True)
        else:
            process = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        if ignore_errors:
            simple_print_warning(f"Command failed but ignoring: {command}")
            return True
        simple_print_error(f"Error executing command: {command}")
        simple_print_error(f"Return Code: {e.returncode}")
        if not show_output and hasattr(e, 'stdout') and e.stdout:
            simple_print_error(f"Output:\n{e.stdout}")
        if not show_output and hasattr(e, 'stderr') and e.stderr:
            simple_print_error(f"Error Output:\n{e.stderr}")
        return False
    except KeyboardInterrupt:
        simple_print_warning("\nCommand interrupted by user.")
        raise

# --- Placeholder Functions for CLI flags ---
def show_help():
    simple_print_header("A.N.U.S. Installer Help")
    simple_print_info("Usage: sudo python3 setup_anus_app.py [flag]")
    simple_print_info("Flags:")
    simple_print_info("  -menu            : Launch the interactive curses menu.")
    simple_print_info("  -uninstall       : Uninstall the application.")
    simple_print_info("  -reinstall       : Uninstall and then reinstall the application.")
    simple_print_info("  -clear-database  : Clear the application database.")
    simple_print_info("  -status          : Check the status of the background service.")
    simple_print_info("  -logs            : View the latest logs from the background service.")
    simple_print_info("  -verbose         : Show detailed command output during installation.")
    simple_print_info("  (no flags)       : Run a standard installation or update.")

def simple_uninstall(confirm=True):
    simple_print_header("Uninstalling A.N.U.S.")
    if confirm:
        simple_print_warning("This will permanently remove the A.N.U.S. application, configurations, and all data.")
        response = input("Are you sure you want to continue? (y/N): ").lower()
        if response != 'y':
            simple_print_info("Uninstall cancelled.")
            return

    simple_print_info("Stopping and disabling services...")
    simple_run_command(f"sudo systemctl stop {SERVICE_NAME}", ignore_errors=True, show_output=False)
    simple_run_command(f"sudo systemctl disable {SERVICE_NAME}", ignore_errors=True, show_output=False)

    simple_print_info("Removing systemd service file...")
    if os.path.exists(SYSTEMD_SERVICE_PATH):
        simple_run_command(f"sudo rm {SYSTEMD_SERVICE_PATH}", ignore_errors=True)
    simple_run_command("sudo systemctl daemon-reload", show_output=False)

    simple_print_info("Disabling Apache site...")
    simple_run_command(f"sudo a2dissite anus.conf", ignore_errors=True, show_output=False)
    if os.path.exists(APACHE_CONF_PATH):
        simple_run_command(f"sudo rm {APACHE_CONF_PATH}", ignore_errors=True)
    simple_run_command("sudo systemctl reload apache2", show_output=False)

    simple_print_info("Removing application and data directories...")
    if os.path.isdir(APP_DIR):
        simple_run_command(f"sudo rm -rf {APP_DIR}", ignore_errors=True)
    if os.path.isdir(DB_DIR):
        simple_run_command(f"sudo rm -rf {DB_DIR}", ignore_errors=True)

    simple_print_info("Removing sudoers permission file...")
    if os.path.exists(SUDOERS_FILE):
        simple_run_command(f"sudo rm {SUDOERS_FILE}", ignore_errors=True)
    
    simple_print_success("\nA.N.U.S. has been successfully uninstalled.")

def simple_clear_database():
    simple_print_header("Clearing A.N.U.S. Database")
    simple_print_warning("This will permanently delete all collected metrics and data.")
    response = input("Are you sure you want to continue? (y/N): ").lower()
    if response != 'y':
        simple_print_info("Database clear cancelled.")
        return

    simple_print_info("Stopping service to release database file...")
    simple_run_command(f"sudo systemctl stop {SERVICE_NAME}", ignore_errors=True, show_output=False)

    simple_print_info("Deleting database file...")
    if os.path.exists(DB_FILE):
        simple_run_command(f"sudo rm {DB_FILE}", ignore_errors=True)
    
    simple_print_info("Re-initializing database schema...")
    if setup_database(simple_run_command, simple_print_info, simple_print_success, simple_print_error, simple_print_warning):
        simple_print_success("Database schema re-initialized successfully.")
    else:
        simple_print_error("Failed to re-initialize the database.")

    simple_print_info("Restarting service...")
    simple_run_command(f"sudo systemctl start {SERVICE_NAME}", show_output=False)
    simple_print_success("\nDatabase has been cleared and service is running.")

# --- Core Logic Functions ---
def get_user():
    # This function is critical for finding the original user who ran 'sudo'
    return os.getenv("SUDO_USER", os.getenv("USER"))

def get_file_version(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            first_line = f.readline()
            match = re.search(r'v(\d+\.\d+\.\d+)', first_line)
            if match:
                return f"v{match.group(1)}"
    except Exception:
        return None
    return None

def get_fqdn():
    """Gets the fully qualified domain name using the hostname -f command."""
    try:
        # The 'hostname -f' command is generally more reliable than socket.getfqdn()
        fqdn = subprocess.check_output(['hostname', '-f'], text=True).strip()
        if fqdn:
            return fqdn
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback if 'hostname -f' fails
        return socket.getfqdn()
    # Fallback if command returns empty string or for other issues
    return socket.getfqdn()

def detect_hostname():
    """Detects hostname using the more reliable get_fqdn method."""
    return get_fqdn()

def detect_ssl_certs(print_info_func, print_warning_func):
    short_hostname = socket.gethostname()
    fqdn = get_fqdn() # Use the new reliable function
    print_info_func(f"Detected hostname: {short_hostname} (FQDN: {fqdn})")
    letsencrypt_dir = "/etc/letsencrypt/live"
    
    if not os.path.isdir(letsencrypt_dir):
        return fqdn, None, None

    potential_path = os.path.join(letsencrypt_dir, fqdn)
    if os.path.isdir(potential_path):
        cert_path = os.path.join(potential_path, "fullchain.pem")
        key_path = os.path.join(potential_path, "privkey.pem")
        if os.path.exists(cert_path) and os.path.exists(key_path):
            print_info_func(f"Found exact SSL cert match for FQDN: {fqdn}")
            return fqdn, cert_path, key_path

    subdirs = [d for d in os.listdir(letsencrypt_dir) if os.path.isdir(os.path.join(letsencrypt_dir, d))]
    for dir_name in subdirs:
        if dir_name.startswith(short_hostname + '.'):
            cert_path = os.path.join(letsencrypt_dir, dir_name, "fullchain.pem")
            key_path = os.path.join(letsencrypt_dir, dir_name, "privkey.pem")
            if os.path.exists(cert_path) and os.path.exists(key_path):
                print_warning_func(f"No exact FQDN match found. Found partial match for '{short_hostname}': {dir_name}")
                return dir_name, cert_path, key_path

    if subdirs:
        first_dir = subdirs[0]
        cert_path = os.path.join(letsencrypt_dir, first_dir, "fullchain.pem")
        key_path = os.path.join(letsencrypt_dir, first_dir, "privkey.pem")
        if os.path.exists(cert_path) and os.path.exists(key_path):
            print_warning_func(f"No matching cert found. Falling back to first available: {first_dir}")
            return first_dir, cert_path, key_path
    
    return fqdn, None, None

def get_ssl_cert_details(cert_path):
    if not cert_path or not os.path.exists(cert_path):
        return None
    details = {}
    try:
        issuer_line = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -issuer", shell=True, text=True).strip()
        issuer_match = re.search(r'CN\s?=\s?([^,]+)', issuer_line)
        details['issuer'] = issuer_match.group(1) if issuer_match else "Unknown"

        details['start_date'] = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -startdate", shell=True, text=True).split('=')[1].strip()
        details['end_date'] = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -enddate", shell=True, text=True).split('=')[1].strip()
        details['fingerprint'] = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -fingerprint -sha256", shell=True, text=True).split('=')[1].strip()
        for key in ['start_date', 'end_date']:
            dt_obj = datetime.strptime(details[key], '%b %d %H:%M:%S %Y %Z')
            details[key] = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return None
    return details

def setup_database(run_command_func, print_info_func, print_success_func, print_error_func, print_warning_func):
    """Creates the database and tables if they don't exist."""
    print_info_func("Setting up database...")
    try:
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)
            run_command_func(f"sudo chown www-data:www-data {DB_DIR}", show_output=False)
            run_command_func(f"sudo chmod 775 {DB_DIR}", show_output=False)

        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL,
                    status TEXT, dns_info TEXT, traceroute_info TEXT,
                    timestamp INTEGER, packet_loss REAL, is_on_demand BOOLEAN DEFAULT 0
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    startTime INTEGER NOT NULL,
                    endTime INTEGER
                )
            """)
            conn.commit()
        
        run_command_func(f"sudo chown www-data:www-data {DB_FILE}", show_output=False)
        run_command_func(f"sudo chmod 664 {DB_FILE}", show_output=False)
        print_success_func("Database setup complete.")
        return True
    except Exception as e:
        print_error_func(f"Failed to create database schema: {e}")
        return False

# --- Installation Step Functions (UI Agnostic) ---
def install_prerequisites(run_command_func, print_info_func, print_success_func, print_error_func, print_warning_func):
    print_info_func("Installing prerequisites...")
    packages = [
        "python3", "python3-pip", "apache2", 
        "php", "libapache2-mod-php", "php-sqlite3", 
        "traceroute", "dnsutils", "iproute2", "wget", "sqlite3", "nmap", "libcap2-bin", "iputils-ping",
        "libncursesw5-dev", "screen" 
    ]
    if not run_command_func("sudo apt-get update") or \
       not run_command_func(f"sudo apt-get install -y {' '.join(packages)}"):
        print_error_func("Failed to install one or more prerequisites.")
        return False
    print_success_func("Prerequisites installed successfully.")
    return True

def download_assets(run_command_func, print_info_func, print_error_func, print_success_func, print_warning_func):
    print_info_func("Downloading self-hosted assets...")
    if not run_command_func(f"sudo mkdir -p {ASSETS_DIR}", show_output=False): return False
    
    for filename, url in ASSETS.items():
        local_path = os.path.join(ASSETS_DIR, filename)
        if not os.path.exists(local_path):
            print_info_func(f"  Downloading {filename}...")
            user_agent_flag = "-U 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'" if "googleapis" in url else ""
            if not run_command_func(f"sudo wget -q -o /dev/null {user_agent_flag} -O {local_path} '{url}'", show_output=False):
                print_error_func(f"Failed to download {filename}. Please check your internet connection.")
                return False
        else:
            print_info_func(f"  {filename} already exists. Skipping download.")
    print_success_func("All assets downloaded successfully.")
    return True

def setup_directories_and_files(run_command_func, print_info_func, print_success_func, print_error_func, print_warning_func):
    print_info_func("Setting up directories...")
    run_command_func(f"sudo mkdir -p {APP_DIR}", show_output=False)
    run_command_func(f"sudo mkdir -p {ASSETS_DIR}", show_output=False)
    run_command_func(f"sudo mkdir -p {DB_DIR}", show_output=False)
    if not os.path.exists(CLIENT_IP_FILE):
        run_command_func(f"sudo touch {CLIENT_IP_FILE}", show_output=False)
    if not os.path.exists(DB_FILE):
        run_command_func(f"sudo touch {DB_FILE}", show_output=False)
    if not os.path.exists(os.path.join(ASSETS_DIR, 'style.css')):
        run_command_func(f"sudo touch {os.path.join(ASSETS_DIR, 'style.css')}", show_output=False)
    print_success_func("Directories and files created.")
    return True

def copy_files(run_command_func, print_info_func, print_warning_func, print_error_func, print_success_func):
    print_info_func("Copying application files...")
    current_dir = os.path.dirname(os.path.realpath(__file__))
    # These explicit lists ensure that the 'support' directory and its contents
    # are NOT copied to the web application directory.
    app_files = ["index.html", "ping.php", PYTHON_SERVICE_FILE]
    web_root_files = ["README.md", "LICENSE"]
    asset_files = ["bruh.mp3", "up.mp3", "targets.json", "fuzzy_sayings.json", "themes.json", "animated-logo.svg", "anus.conf", "style.css"]

    print_info_func("  Checking versions of existing files...")
    for file_name in app_files:
        installed_path = os.path.join(APP_DIR, file_name)
        installed_version = get_file_version(installed_path)
        if installed_version:
            if installed_version == VERSION:
                print_info_func(f"  - {file_name} is up to date ({VERSION}).")
            else:
                print_warning_func(f"  - {file_name} will be upgraded from {installed_version} to {VERSION}.")
        else:
            print_info_func(f"  - {file_name} will be installed ({VERSION}).")

    for file_name in app_files:
        source_path = os.path.join(current_dir, file_name)
        if not os.path.exists(source_path):
            print_error_func(f"Required file '{file_name}' not found at {source_path}.")
            return False
        if not run_command_func(f"sudo cp {source_path} {APP_DIR}/", show_output=False): return False
            
    web_root = os.path.join(APP_DIR, "..")
    for file_name in web_root_files:
        source_path = os.path.join(current_dir, file_name)
        if not os.path.exists(source_path):
            print_error_func(f"Required file '{file_name}' not found at {source_path}.")
            return False
        if not run_command_func(f"sudo cp {source_path} {web_root}/", show_output=False): return False
            
    for file_name in asset_files:
        source_path = os.path.join(current_dir, "assets", file_name)
        if not os.path.exists(source_path):
            print_warning_func(f"File '{file_name}' not found at {source_path}. Skipping.")
            continue
        if not run_command_func(f"sudo cp {source_path} {ASSETS_DIR}/", show_output=False): return False

    if not run_command_func(f"sudo chmod +x {APP_DIR}/{PYTHON_SERVICE_FILE}", show_output=False): return False
    print_success_func("Application files copied.")
    return True

def set_permissions(run_command_func, print_info_func, print_success_func, print_error_func, print_warning_func):
    print_info_func("Setting final file permissions...")
    if not run_command_func(f"sudo chown -R www-data:www-data {DB_DIR}", show_output=False): return False
    if not run_command_func(f"sudo chmod -R 775 {DB_DIR}", show_output=False): return False
    if not run_command_func(f"sudo chown -R www-data:www-data {APP_DIR}", show_output=False): return False
    if not run_command_func(f"sudo chmod -R 755 {APP_DIR}", show_output=False): return False
    web_root = os.path.join(APP_DIR, "..")
    if os.path.exists(os.path.join(web_root, "README.md")):
        if not run_command_func(f"sudo chmod 664 {os.path.join(web_root, 'README.md')}", show_output=False): return False
        if not run_command_func(f"sudo chown www-data:www-data {os.path.join(web_root, 'README.md')}", show_output=False): return False
    if os.path.exists(os.path.join(web_root, "LICENSE")):
        if not run_command_func(f"sudo chmod 664 {os.path.join(web_root, 'LICENSE')}", show_output=False): return False
        if not run_command_func(f"sudo chown www-data:www-data {os.path.join(web_root, 'LICENSE')}", show_output=False): return False
    if os.path.exists(os.path.join(ASSETS_DIR, "targets.json")):
        if not run_command_func(f"sudo chmod 664 {ASSETS_DIR}/targets.json", show_output=False): return False
    print_success_func("File permissions set correctly.")
    return True

def configure_apache(run_command_func, print_info_func, print_warning_func, print_error_func, print_success_func):
    print_info_func("Configuring Apache...")
    hostname, cert_file, key_file = detect_ssl_certs(print_info_func, print_warning_func)

    if not cert_file or not key_file:
        print_error_func("Could not find SSL certificate files in /etc/letsencrypt/live/")
        print_error_func("Please ensure you have a valid Let's Encrypt certificate for your server.")
        return False
        
    try:
        with open("ssl_info.json.tmp", "w") as f:
            json.dump({"cert_path": cert_file}, f)
        if not run_command_func(f"sudo cp ssl_info.json.tmp {SSL_INFO_CONFIG_FILE}", show_output=False): return False
        os.remove("ssl_info.json.tmp")
    except Exception as e:
        print_error_func(f"Failed to write SSL info file: {e}")
        return False

    apache_conf_content = f"""
<VirtualHost *:80>
    ServerName {hostname}
    Redirect permanent "/" "https://{hostname}/anus/"
</VirtualHost>
<VirtualHost *:443>
    ServerName {hostname}
    DocumentRoot /var/www/html
    AliasMatch "^/anus(.*)" "{APP_DIR}$1"
    <Directory "{APP_DIR}">
        Options -Indexes
        AllowOverride None
        Require all granted
        DirectoryIndex index.html
    </Directory>
    <Directory "/var/www/html">
        Require all granted
    </Directory>
    <Directory "{DB_DIR}">
        Require all denied
    </Directory>
    SSLEngine on
    SSLCertificateFile {cert_file}
    SSLCertificateKeyFile {key_file}
    ErrorLog ${{APACHE_LOG_DIR}}/error.log
    CustomLog ${{APACHE_LOG_DIR}}/access.log combined
</VirtualHost>
"""
    try:
        with open("anus.conf.tmp", "w") as f:
            f.write(apache_conf_content)
        if not run_command_func(f"sudo cp anus.conf.tmp {APACHE_CONF_PATH}", show_output=False): return False
        os.remove("anus.conf.tmp")
    except Exception as e:
        print_error_func(f"Failed to write Apache config file: {e}")
        return False

    if not run_command_func("sudo a2ensite anus.conf", ignore_errors=True): return False
    if not run_command_func("sudo a2enmod ssl alias", ignore_errors=True): return False
    print_success_func("Apache configured.")
    return True

def setup_systemd_service(run_command_func, print_info_func, print_success_func, print_error_func, print_warning_func):
    print_info_func(f"Setting up systemd service: {SERVICE_NAME}...")
    service_content = f"""
[Unit]
Description=A.N.U.S. Network Monitoring Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 {APP_DIR}/{PYTHON_SERVICE_FILE}
WorkingDirectory={APP_DIR}
StandardOutput=inherit
StandardError=inherit
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
"""
    with open(f"{SERVICE_NAME}.service.tmp", "w") as f: f.write(service_content)
    
    if not run_command_func(f"sudo mv {SERVICE_NAME}.service.tmp {SYSTEMD_SERVICE_PATH}", show_output=False): return False
    if not run_command_func("sudo systemctl daemon-reload", show_output=False): return False
    if not run_command_func(f"sudo systemctl enable {SERVICE_NAME}.service", show_output=False): return False
    if not run_command_func(f"sudo systemctl restart {SERVICE_NAME}.service", show_output=False): return False
    print_success_func("Systemd service created and started.")
    return True

def apply_system_tweaks(run_command_func, print_info_func, print_success_func, print_error_func, print_warning_func):
    print_info_func("Applying system tweaks...")
    nmap_path = shutil.which("nmap")
    traceroute_path = shutil.which("traceroute")

    if not nmap_path or not traceroute_path:
        print_error_func("Could not find 'nmap' or 'traceroute' executables.")
        return False

    sudoers_file = "/etc/sudoers.d/anus-permissions"
    sudoers_content = (
        f"www-data ALL=(ALL) NOPASSWD: {nmap_path}\n"
        f"www-data ALL=(ALL) NOPASSWD: {traceroute_path}\n"
    )
    
    print_info_func(f"  Creating sudoers file at {sudoers_file}...")
    try:
        with open("anus-permissions.tmp", "w") as f:
            f.write(sudoers_content)
        
        if not run_command_func(f"sudo mv anus-permissions.tmp {sudoers_file}", show_output=False): return False
        if not run_command_func(f"sudo chown root:root {sudoers_file}", show_output=False): return False
        if not run_command_func(f"sudo chmod 0440 {sudoers_file}", show_output=False): return False
        
        print_success_func("Sudoers rule for nmap & traceroute created successfully.")
    except Exception as e:
        print_error_func(f"Failed to create or set permissions on sudoers file: {e}")
        return False

    return True

# --- Curses UI Class ---
class CursesUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.menu = ["Install / Update", "Re-install", "View Service Status", "View Service Logs", "Clear Output", "Clear Database", "Uninstall", "Exit"]
        self.current_row = 0
        self.init_curses()

    def init_curses(self):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selected
        curses.init_pair(2, curses.COLOR_RED, -1)     # Error / Destructive
        curses.init_pair(3, curses.COLOR_YELLOW, -1)   # Warning / Re-install
        curses.init_pair(4, curses.COLOR_CYAN, -1)     # Info / View
        curses.init_pair(5, curses.COLOR_GREEN, -1)    # Success / Install
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)  # Borders
        curses.init_pair(7, curses.COLOR_WHITE, -1)    # Default Text
        curses.init_pair(8, curses.COLOR_BLUE, -1)     # Panel titles

    def draw_layout(self, menu_visible=True):
        h, w = self.stdscr.getmaxyx()
        self.stdscr.bkgd(' ', curses.color_pair(7))
        self.stdscr.clear()

        header_text = f"A.N.U.S. Setup Utility ({VERSION})"
        self.stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        self.stdscr.addstr(0, 0, " " * w)
        self.stdscr.addstr(0, w // 2 - len(header_text) // 2, header_text)
        self.stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
        
        footer_text = "Use ↑/↓ to navigate, Enter to select, Q to quit." if menu_visible else "Installation in progress..."
        self.stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        self.stdscr.addstr(h - 1, 0, " " * (w-1))
        self.stdscr.addstr(h - 1, w // 2 - len(footer_text) // 2, footer_text)
        self.stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        if menu_visible:
            menu_width = 30
            status_height = 9
            self.menu_win = curses.newwin(h - 2, menu_width, 1, 1)
            self.output_win = curses.newwin(h - 2 - status_height, w - menu_width - 2, 1, menu_width + 1)
            self.status_win = curses.newwin(status_height, w - menu_width - 2, h - 1 - status_height, menu_width + 1)
            
            self.menu_win.box()
            self.status_win.box()
            self.menu_win.addstr(0, 2, " Menu ", curses.color_pair(8) | curses.A_BOLD)
            self.status_win.addstr(0, 2, " System Info ", curses.color_pair(8) | curses.A_BOLD)
        else:
            self.output_win = curses.newwin(h - 2, w - 2, 1, 1)

        self.progress_win = curses.newwin(3, w - 2, h - 4, 1)
        self.output_win.box()
        self.output_win.addstr(0, 2, " Output ", curses.color_pair(8) | curses.A_BOLD)
        self.output_win.scrollok(True)

        self.stdscr.refresh()
        if menu_visible:
            self.menu_win.refresh()
            self.status_win.refresh()
        self.output_win.refresh()


    def update_status_panel(self):
        self.status_win.clear()
        self.status_win.box()
        self.status_win.addstr(0, 2, " System Info ", curses.color_pair(8) | curses.A_BOLD)
        hostname, cert_file, _ = detect_ssl_certs(lambda m: None, lambda m: None)
        self.status_win.addstr(1, 2, f"Hostname: {hostname}", curses.color_pair(7))
        
        cert_details = get_ssl_cert_details(cert_file)
        if cert_details:
            self.status_win.addstr(2, 2, "SSL Cert:", curses.color_pair(5))
            self.status_win.addstr(3, 4, f"Issuer: {cert_details['issuer']}", curses.color_pair(7))
            self.status_win.addstr(4, 4, f"Issued: {cert_details['start_date']}", curses.color_pair(7))
            
            end_date = datetime.strptime(cert_details['end_date'], '%Y-%m-%d %H:%M:%S')
            days_left = (end_date - datetime.now()).days
            
            color = curses.color_pair(5) # Green
            if days_left < 14: color = curses.color_pair(3) # Yellow
            if days_left < 7: color = curses.color_pair(2) # Red

            self.status_win.addstr(5, 4, f"Expires: {cert_details['end_date']} (", curses.color_pair(7))
            self.status_win.addstr(f"{days_left} days left", color | curses.A_BOLD)
            self.status_win.addstr(")", curses.color_pair(7))
            self.status_win.addstr(6, 4, f"Fingerprint: {cert_details['fingerprint'][:32]}...", curses.color_pair(7))
        else:
            self.status_win.addstr(2, 2, "SSL Cert: Not Found", curses.color_pair(2))
        
        self.status_win.refresh()
        
    def draw_menu(self):
        h, w = self.menu_win.getmaxyx()
        self.menu_win.clear()
        self.menu_win.box()
        self.menu_win.addstr(0, 2, " Menu ", curses.color_pair(8) | curses.A_BOLD)

        menu_colors = {
            "Install / Update": curses.color_pair(5),
            "Re-install": curses.color_pair(3),
            "View Service Status": curses.color_pair(4),
            "View Service Logs": curses.color_pair(4),
            "Clear Output": curses.color_pair(7),
            "Clear Database": curses.color_pair(2),
            "Uninstall": curses.color_pair(2),
            "Exit": curses.color_pair(7)
        }

        for idx, row in enumerate(self.menu):
            x = w // 2 - len(row) // 2
            y = (h // 2 - len(self.menu) // 2) + idx
            color = menu_colors.get(row, curses.color_pair(7))
            if idx == self.current_row:
                self.menu_win.attron(curses.color_pair(1))
                self.menu_win.addstr(y, x, f" {row} ")
                self.menu_win.attroff(curses.color_pair(1))
            else:
                self.menu_win.addstr(y, x, row, color)
        self.menu_win.refresh()

    def update_progress_bar(self, current_step, total_steps, message=""):
        h, w = self.progress_win.getmaxyx()
        self.progress_win.clear()
        self.progress_win.box()
        
        percent = int((current_step / total_steps) * 100)
        bar_width = w - 8
        filled_len = int(bar_width * percent / 100)
        
        bar = '█' * filled_len + '─' * (bar_width - filled_len)
        self.progress_win.addstr(1, 2, f"[{bar}] {percent}%", curses.color_pair(5))
        self.progress_win.addstr(0, 2, f" Progress: {message[:bar_width-10]} ", curses.color_pair(8) | curses.A_BOLD)
        self.progress_win.refresh()

    def run_command_curses(self, win, command, ignore_errors=False):
        if VERBOSE_MODE:
            win.addstr(f"\n$ ", curses.color_pair(4) | curses.A_BOLD)
            win.addstr(f"{command}\n", curses.color_pair(7))
            win.refresh()
        
        try:
            process = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, universal_newlines=True
            )

            stdout_lines = []
            stderr_lines = []

            def read_stream(stream, line_list, color):
                for line in iter(stream.readline, ''):
                    line_list.append(line)
                    if VERBOSE_MODE:
                        for wrapped_line in textwrap.wrap(line.strip(), win.getmaxyx()[1] - 4):
                            win.addstr(f"  {wrapped_line}\n", color)
                        win.refresh()

            stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, curses.color_pair(7)))
            stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, curses.color_pair(3)))

            stdout_thread.start()
            stderr_thread.start()
            
            stdout_thread.join()
            stderr_thread.join()
            
            return_code = process.wait()

            if return_code != 0 and not ignore_errors:
                win.addstr(f"\nError: Command failed with exit code {return_code}\n", curses.color_pair(2) | curses.A_BOLD)
                if not VERBOSE_MODE:
                    if stdout_lines:
                        win.addstr("--- STDOUT ---\n", curses.color_pair(2))
                        for line in stdout_lines:
                            for wrapped_line in textwrap.wrap(line.strip(), win.getmaxyx()[1] - 4):
                                win.addstr(f"  {wrapped_line}\n", curses.color_pair(7))
                    if stderr_lines:
                        win.addstr("--- STDERR ---\n", curses.color_pair(2))
                        for line in stderr_lines:
                            for wrapped_line in textwrap.wrap(line.strip(), win.getmaxyx()[1] - 4):
                                win.addstr(f"  {wrapped_line}\n", curses.color_pair(2))
                win.refresh()
                return False
                
            return True
        except Exception as e:
            win.addstr(f"\nFATAL ERROR running command: {e}\n", curses.color_pair(2) | curses.A_BOLD)
            win.refresh()
            return False

    def show_output_window(self, title, action_func, menu_visible=True):
        self.draw_layout(menu_visible=menu_visible) 
        
        self.output_win.clear()
        self.output_win.box()
        self.output_win.addstr(0, 2, f" {title} ", curses.color_pair(8) | curses.A_BOLD)
        self.output_win.refresh()
        
        action_func(self.output_win)
        
        if menu_visible:
            self.output_win.addstr("\n\nPress any key to return to the menu.", curses.color_pair(3) | curses.A_BOLD)
        else:
            self.output_win.addstr("\n\nInstallation finished. Press any key to exit.", curses.color_pair(3) | curses.A_BOLD)

        self.output_win.getch()

    def run(self):
        self.draw_layout()
        self.update_status_panel()
        self.draw_menu()
        
        action_on_exit = None
        try:
            while True:
                key = self.stdscr.getch()

                if key == curses.KEY_UP and self.current_row > 0:
                    self.current_row -= 1
                elif key == curses.KEY_DOWN and self.current_row < len(self.menu) - 1:
                    self.current_row += 1
                elif key == ord('q'):
                    break
                elif key == curses.KEY_ENTER or key in [10, 13]:
                    selected_action_title = self.menu[self.current_row]
                    
                    if selected_action_title == "Exit":
                        break
                    
                    if selected_action_title == "Clear Output":
                        self.output_win.clear()
                        self.output_win.box()
                        self.output_win.addstr(0, 2, " Output ", curses.color_pair(8) | curses.A_BOLD)
                        self.output_win.refresh()
                        continue

                    if selected_action_title in ["Uninstall", "Clear Database", "Re-install"]:
                        action_on_exit = selected_action_title.lower().replace("-", "_").replace(" ", "_")
                        break
                    
                    self.output_win.clear()
                    self.output_win.box()
                    self.output_win.addstr(0, 2, f" {selected_action_title} ", curses.color_pair(8) | curses.A_BOLD)
                    self.output_win.refresh()
                    
                    def c_print_success(msg): self.output_win.addstr(f"✔ {msg}\n", curses.color_pair(5)); self.output_win.refresh()
                    def c_print_error(msg): self.output_win.addstr(f"✖ {msg}\n", curses.color_pair(2)); self.output_win.refresh()
                    def c_print_info(msg): self.output_win.addstr(f"{msg}\n", curses.color_pair(7)); self.output_win.refresh()
                    def c_print_warning(msg): self.output_win.addstr(f"⚠ {msg}\n", curses.color_pair(3)); self.output_win.refresh()
                    def c_run_command(cmd, ignore_errors=False, show_output=True):
                        return self.run_command_curses(self.output_win, cmd, ignore_errors)

                    if selected_action_title == "Install / Update":
                        install_steps = [
                            ("Installing prerequisites", lambda: install_prerequisites(c_run_command, c_print_info, c_print_success, c_print_error, c_print_warning)),
                            ("Setting up directories", lambda: setup_directories_and_files(c_run_command, c_print_info, c_print_success, c_print_error, c_print_warning)),
                            ("Downloading assets", lambda: download_assets(c_run_command, c_print_info, c_print_error, c_print_success, c_print_warning)),
                            ("Copying application files", lambda: copy_files(c_run_command, c_print_info, c_print_warning, c_print_error, c_print_success)),
                            ("Setting permissions", lambda: set_permissions(c_run_command, c_print_info, c_print_success, c_print_error, c_print_warning)),
                            ("Configuring Apache", lambda: configure_apache(c_run_command, c_print_info, c_print_warning, c_print_error, c_print_success)),
                            ("Applying system tweaks", lambda: apply_system_tweaks(c_run_command, c_print_info, c_print_success, c_print_error, c_print_warning)),
                            ("Setting up database", lambda: setup_database(c_run_command, c_print_info, c_print_success, c_print_error, c_print_warning)),
                            ("Setting up systemd service", lambda: setup_systemd_service(c_run_command, c_print_info, c_print_success, c_print_error, c_print_warning)),
                            ("Finalizing setup", lambda: c_run_command("sudo systemctl restart apache2", ignore_errors=True, show_output=False))
                        ]
                        
                        for i, (msg, step_func) in enumerate(install_steps):
                            self.update_progress_bar(i, len(install_steps), msg)
                            c_print_info(f"\n--- {msg} ---")
                            if not step_func():
                                c_print_error(f"\n--- Step '{msg}' failed. Aborting installation. ---")
                                break
                        else:
                            self.update_progress_bar(len(install_steps), len(install_steps), "Complete!")
                            c_print_success(f"\n--- A.N.U.S. {VERSION} Installation/Update complete! ---")

                    elif selected_action_title == "View Service Status":
                        try:
                            command = f"sudo systemctl status {SERVICE_NAME}.service --no-pager"
                            result = subprocess.run(command, shell=True, capture_output=True, text=True)
                            output = result.stdout + result.stderr
                            for line in output.splitlines():
                                self.output_win.addstr(f"{line}\n")
                            self.output_win.refresh()
                        except Exception as e:
                            self.output_win.addstr(f"Failed to get service status: {e}\n", curses.color_pair(2))

                    elif selected_action_title == "View Service Logs":
                        try:
                            command = f"sudo journalctl -u {SERVICE_NAME}.service -n 50 --no-pager"
                            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
                            for line in result.stdout.splitlines():
                                self.output_win.addstr(f"{line}\n")
                            self.output_win.refresh()
                        except Exception as e:
                            self.output_win.addstr(f"Failed to get service logs: {e}\n", curses.color_pair(2))
                    
                    self.output_win.addstr("\n\nPress any key to return to the menu.", curses.color_pair(3) | curses.A_BOLD)
                    self.output_win.getch()
                    self.draw_layout()
                    self.update_status_panel()
                    self.draw_menu()

                self.draw_menu()
        except KeyboardInterrupt:
            pass
        
        return action_on_exit

# --- Screen Detection Functions ---
def is_screen_installed() -> bool:
    """Checks if the 'screen' package is installed using dpkg or which."""
    try:
        result = subprocess.run(['dpkg', '-s', 'screen'], capture_output=True)
        return result.returncode == 0
    except FileNotFoundError:
        return shutil.which('screen') is not None

def check_user_screen_session() -> (bool, str):
    """
    Checks the ORIGINAL user's screen session using the helper script.
    
    This function now executes the helper script, captures its output,
    and returns both the boolean result and the full output for display.
    """
    original_user = get_user()
    if not original_user or original_user == 'root':
        is_in_screen = 'STY' in os.environ
        output = "Running as root without a SUDO_USER. Standard environment check performed."
        return is_in_screen, output

    try:
        current_dir = os.path.dirname(os.path.realpath(__file__))
        helper_script_path = os.path.join(current_dir, "support", "screen_test.py")

        if not os.path.exists(helper_script_path):
            error_msg = f"Screen helper script not found at: {helper_script_path}"
            return False, error_msg

        command = ["sudo", "-u", original_user, sys.executable, helper_script_path]
        
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        script_output = result.stdout
        is_in_screen = "✅ Status: Running inside a GNU screen session." in script_output
        
        return is_in_screen, script_output

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        error_output = f"Failed to execute screen check helper: {e}"
        if hasattr(e, 'stderr'):
            error_output += f"\nStderr: {e.stderr}"
        return False, error_output

# --- Main execution block ---
def main():
    if os.geteuid() != 0:
        simple_print_error("This script must be run as root. Please use sudo.")
        sys.exit(1)

    args = {arg.lstrip('-/').lower() for arg in sys.argv[1:]}
    
    # --- START: Integrated Screen Logic (with --already-in-screen flag) ---
    if 'menu' in args and '--already-in-screen' not in args:
        simple_print_header("Screen Session Management")
        
        is_in_screen, check_output = check_user_screen_session()
        print(check_output)

        if not is_in_screen:
            if not is_screen_installed():
                simple_print_info("The 'screen' package is not installed. Attempting to install...")
                if not simple_run_command("sudo apt-get update", show_output=VERBOSE_MODE) or \
                   not simple_run_command("sudo apt-get install -y screen"):
                    simple_print_error("Failed to install 'screen'. The interactive menu requires it.")
                    sys.exit(1)
                simple_print_success("'screen' package has been installed successfully.")
            
            simple_print_info("Not in a screen session. Relaunching the installer inside 'screen'...")
            time.sleep(2)

            script_path = os.path.realpath(__file__)
            python_executable = sys.executable
            
            # Relaunch, adding the --already-in-screen flag to prevent a loop
            command_to_run = ['screen', '-S', 'anus_installer', 'sudo', python_executable, script_path] + sys.argv[1:] + ['--already-in-screen']
            
            try:
                os.execvp('screen', command_to_run)
            except FileNotFoundError:
                simple_print_error("Could not execute 'screen'. Please ensure it is in your system's PATH.")
                sys.exit(1)
            return
        else:
            simple_print_success("Continuing installer in the current screen session.")
            time.sleep(1)
    
    # If the flag is present, remove it so it doesn't interfere with other logic
    if '--already-in-screen' in sys.argv:
        sys.argv.remove('--already-in-screen')
        args.discard('already-in-screen')
    # --- END: Integrated Screen Logic ---

    # Handle non-interactive flags
    if 'help' in args:
        show_help()
        sys.exit(0)
    if 'uninstall' in args:
        simple_uninstall()
        sys.exit(0)
    if 'reinstall' in args:
        simple_uninstall(confirm=False)
        # We need a non-curses install function here
        simple_install_or_update_with_funcs() 
        sys.exit(0)
    if 'clear-database' in args or 'cleardatabase' in args:
        simple_clear_database()
        sys.exit(0)
    if 'status' in args:
        os.system(f"sudo systemctl status {SERVICE_NAME}.service")
        sys.exit(0)
    if 'logs' in args:
        os.system(f"sudo journalctl -u {SERVICE_NAME}.service -n 50")
        sys.exit(0)
    
    is_menu_mode = 'menu' in args
    is_direct_install = not args or ('verbose' in args and len(args) == 1)

    try:
        if is_menu_mode:
            if CURSES_AVAILABLE:
                def curses_main_loop(stdscr):
                    ui = CursesUI(stdscr)
                    return ui.run()
                action_on_exit = curses.wrapper(curses_main_loop)
                
                if action_on_exit == "uninstall":
                    simple_uninstall()
                elif action_on_exit == "re_install":
                    simple_uninstall(confirm=False)
                    simple_install_or_update_with_funcs()
                elif action_on_exit == "clear_database":
                    simple_clear_database()
            else:
                simple_print_warning("Curses library not found. Falling back to non-interactive mode.")
        elif is_direct_install:
            simple_install_or_update_with_funcs()
        else:
            show_help()
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(0)

def simple_install_or_update_with_funcs():
    simple_print_header(f"Starting A.N.U.S. {VERSION} Installation/Update")
    steps = [
        ("Installing prerequisites", install_prerequisites),
        ("Setting up directories", setup_directories_and_files),
        ("Downloading assets", download_assets),
        ("Copying application files", copy_files),
        ("Setting permissions", set_permissions),
        ("Configuring Apache", configure_apache),
        ("Applying system tweaks", apply_system_tweaks),
        ("Setting up database", setup_database),
        ("Setting up systemd service", setup_systemd_service),
    ]
    
    for i, (msg, step_func) in enumerate(steps):
        simple_print_header(f"Step {i+1}/{len(steps)}: {msg}")
        if not step_func(simple_run_command, simple_print_info, simple_print_success, simple_print_error, simple_print_warning):
            simple_print_error(f"\n--- Step '{msg}' failed. Aborting installation. ---")
            return
        simple_print_success(f"Step '{msg}' complete.")

    simple_print_header("Finalizing Setup")
    simple_run_command("sudo systemctl restart apache2")
    simple_run_command(f"sudo systemctl status {SERVICE_NAME}.service")
    simple_print_success(f"\n--- A.N.U.S. {VERSION} Installation/Update complete! ---")

if __name__ == "__main__":
    main()
