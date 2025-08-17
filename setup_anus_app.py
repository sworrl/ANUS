#!/usr/bin/env python3

import subprocess
import os
import sys
import sqlite3
import time
import socket
import xml.etree.ElementTree as ET

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
VERSION = "v1.2.6"

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

# --- Color Codes for Output ---
class colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_header(message):
    print(f"\n{colors.BOLD}{colors.HEADER}--- {message} ---{colors.ENDC}")

def print_success(message):
    print(f"{colors.GREEN}✔ {message}{colors.ENDC}")

def print_warning(message):
    print(f"{colors.YELLOW}⚠ {message}{colors.ENDC}", file=sys.stderr)

def print_error(message):
    print(f"{colors.RED}✖ {message}{colors.ENDC}", file=sys.stderr)

def print_info(message):
    print(f"{colors.BLUE}{message}{colors.ENDC}")


def run_command(command, ignore_errors=False, show_output=True):
    """Run a shell command and check for errors, printing output."""
    print_info(f"Executing: {command}")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        if show_output and result.stdout: print(result.stdout)
        if show_output and result.stderr: print(result.stderr, file=sys.stderr)
        return True
    except subprocess.CalledProcessError as e:
        if ignore_errors:
            print_warning(f"Command failed but ignoring: {command}")
            return True
        print_error(f"Error executing command: {command}")
        print_error(f"Return Code: {e.returncode}")
        if show_output:
            print_error(f"Output:\n{e.stdout}")
            print_error(f"Error Output:\n{e.stderr}")
        return False

def install_prerequisites():
    """Install required Apache, PHP, and Python packages."""
    print_header("Installing prerequisites")
    # Added 'nmap' to the list of packages.
    packages = [
        "python3", "python3-pip", "apache2", 
        "php", "libapache2-mod-php", "php-sqlite3", 
        "traceroute", "dnsutils", "iproute2", "wget", "sqlite3", "nmap", "libcap2-bin"
    ]
    if not run_command("sudo apt-get update") or \
       not run_command(f"sudo apt-get install -y {' '.join(packages)}"):
        return False
    print_success("Prerequisites installed successfully.")
    return True

def download_assets():
    """Downloads JS/CSS libraries for self-hosting."""
    print_header("Downloading self-hosted assets")
    if not run_command(f"sudo mkdir -p {ASSETS_DIR}"): return False
    
    for filename, url in ASSETS.items():
        local_path = os.path.join(ASSETS_DIR, filename)
        if not os.path.exists(local_path):
            print_info(f"Downloading {filename}...")
            # Use a specific user agent for Google Fonts to get the correct CSS
            user_agent_flag = "-U 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'" if "googleapis" in url else ""
            if not run_command(f"sudo wget {user_agent_flag} -O {local_path} '{url}'"):
                print_error(f"Failed to download {filename}. Please check your internet connection.")
                return False
        else:
            print_info(f"{filename} already exists. Skipping download.")
    print_success("All assets downloaded successfully.")
    return True

def setup_directories_and_files():
    """Create necessary directories and files before copying."""
    print_header("Setting up directories and files")
    if not run_command(f"sudo mkdir -p {APP_DIR}"): return False
    if not run_command(f"sudo mkdir -p {ASSETS_DIR}"): return False
    if not run_command(f"sudo mkdir -p {DB_DIR}"): return False
    if not run_command(f"sudo touch {CLIENT_IP_FILE}"): return False
    if not run_command(f"sudo touch {DB_FILE}"): return False
    print_success("Directories and files created.")
    return True

def copy_files():
    """Copy the application files to the web directory."""
    print_header("Copying application files")
    
    current_dir = os.path.dirname(os.path.realpath(__file__))
    
    # Files to be copied directly to the web app directory
    app_files = ["index.html", "ping.php", PYTHON_SERVICE_FILE]
    
    # Files to be copied to the assets folder
    asset_files = ["bruh.mp3", "up.mp3", "targets.json", "fuzzy_sayings.json", "themes.json", "README.md", "LICENSE", "license.md", "animated-logo.svg", "anus.conf"]

    # Copy main app files
    for file_name in app_files:
        source_path = os.path.join(current_dir, file_name)
        if not os.path.exists(source_path):
            print_error(f"Required file '{file_name}' not found at {source_path}.")
            return False
        if not run_command(f"sudo cp {source_path} {APP_DIR}/"):
            return False
            
    # Copy asset files to assets directory
    for file_name in asset_files:
        source_path = os.path.join(current_dir, "assets", file_name)
        if not os.path.exists(source_path):
            print_warning(f"File '{file_name}' not found at {source_path}. Skipping.")
            continue
        if not run_command(f"sudo cp {source_path} {ASSETS_DIR}/"):
            return False

    if not run_command(f"sudo chmod +x {APP_DIR}/{PYTHON_SERVICE_FILE}"):
        return False
    print_success("Application files copied.")
    return True

def set_permissions():
    """Set final ownership and permissions for all app-related files and directories."""
    print_header("Setting final file permissions")
    if not run_command(f"sudo chown -R www-data:www-data {DB_DIR}"): return False
    if not run_command(f"sudo chmod -R 775 {DB_DIR}"): return False
    if not run_command(f"sudo chown -R www-data:www-data {APP_DIR}"): return False
    if not run_command(f"sudo chmod -R 755 {APP_DIR}"): return False
    if os.path.exists(f"{ASSETS_DIR}/targets.json"):
        if not run_command(f"sudo chmod 664 {ASSETS_DIR}/targets.json"): return False
    print_success("File permissions set correctly.")
    return True

def configure_apache():
    """Create and enable the Apache configuration file."""
    print_header("Configuring Apache")
    apache_conf_content = f"""
<VirtualHost *:80>
    ServerName jengus.wifi.local.falcontechnix.com
    Redirect permanent "/" "https://jengus.wifi.local.falcontechnix.com/anus/"
</VirtualHost>
<VirtualHost *:443>
    ServerName jengus.wifi.local.falcontechnix.com
    DocumentRoot /var/www/html
    AliasMatch "^/anus(.*)" "{APP_DIR}$1"
    <Directory "{APP_DIR}">
        Options -Indexes
        AllowOverride None
        Require all granted
        DirectoryIndex index.html
    </Directory>
    <Directory "{DB_DIR}">
        Require all denied
    </Directory>
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/jengus.wifi.local.falcontechnix.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/jengus.wifi.local.falcontechnix.com/privkey.pem
    ErrorLog ${{APACHE_LOG_DIR}}/error.log
    CustomLog ${{APACHE_LOG_DIR}}/access.log combined
</VirtualHost>
"""
    if not os.path.exists(APACHE_CONF_PATH):
      with open("anus.conf.tmp", "w") as f: f.write(apache_conf_content)
      if not run_command(f"sudo cp anus.conf.tmp {APACHE_CONF_PATH}"): return False
    if not run_command("sudo a2ensite anus.conf", ignore_errors=True): return False
    if not run_command("sudo a2enmod ssl alias", ignore_errors=True): return False
    print_success("Apache configured.")
    return True

def setup_systemd_service():
    """Creates a systemd service file for the Python script and starts it."""
    print_header(f"Setting up systemd service: {SERVICE_NAME}")
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
    
    if not run_command(f"sudo mv {SERVICE_NAME}.service.tmp {SYSTEMD_SERVICE_PATH}"): return False
    if not run_command("sudo systemctl daemon-reload"): return False
    if not run_command(f"sudo systemctl enable {SERVICE_NAME}.service"): return False
    if not run_command(f"sudo systemctl restart {SERVICE_NAME}.service"): return False
    print_success("Systemd service created and started.")
    return True

def apply_system_tweaks():
    """Apply necessary system-level tweaks like permissions for ping and nmap."""
    print_header("Applying system tweaks")
    if not run_command("sudo setcap cap_net_raw+ep /bin/ping"):
        print_warning("Failed to set capabilities on ping. The service might not be able to measure latency.")
    # Set capabilities for nmap to allow it to run without root
    if not run_command("sudo setcap cap_net_raw,cap_net_admin,cap_net_bind_service+ep /usr/bin/nmap"):
        print_warning("Failed to set capabilities on nmap. The service might not be able to perform network scans.")
    if not run_command("sudo apt-get install -y libcap2-bin"):
        print_warning("Failed to install libcap2-bin. Permissions for ping and nmap may not be set correctly.")
    print_success("System tweaks applied.")
    return True

def install_or_update():
    """Run the full installation or update process."""
    print_header(f"Starting A.N.U.S. {VERSION} Installation/Update")
    if not install_prerequisites() or \
       not setup_directories_and_files() or \
       not download_assets() or \
       not copy_files() or \
       not set_permissions() or \
       not configure_apache() or \
       not apply_system_tweaks() or \
       not setup_systemd_service():
        print_error("\n--- Setup failed. Please check the errors above. ---")
        return
    
    print_header("Finalizing Setup")
    run_command("sudo systemctl restart apache2")
    run_command(f"sudo systemctl status {SERVICE_NAME}.service")
    
    print_success(f"\n--- A.N.U.S. {VERSION} Installation/Update complete! ---")
    print("The Apache web server and the Python monitoring service have been configured and restarted.")
    print(f"Your application should be accessible at: https://jengus.wifi.local.falcontechnix.com/anus/")

def uninstall():
    """Remove all components of the A.N.U.S. application."""
    print_header("Uninstalling A.N.U.S.")
    confirm = input(f"{colors.YELLOW}Are you sure you want to completely uninstall A.N.U.S.? This will delete all data. (y/N): {colors.ENDC}")
    if confirm.lower() != 'y':
        print_info("Uninstallation cancelled.")
        return

    print_info("Stopping and disabling service...")
    run_command(f"sudo systemctl stop {SERVICE_NAME}.service", ignore_errors=True)
    run_command(f"sudo systemctl disable {SERVICE_NAME}.service", ignore_errors=True)
    
    print_info("Removing files...")
    if os.path.exists(SYSTEMD_SERVICE_PATH): run_command(f"sudo rm {SYSTEMD_SERVICE_PATH}")
    run_command("sudo systemctl daemon-reload")
    if os.path.exists(APACHE_CONF_PATH):
        run_command(f"sudo a2dissite anus.conf", ignore_errors=True)
        run_command(f"sudo rm {APACHE_CONF_PATH}")
        run_command("sudo systemctl reload apache2")
    if os.path.isdir(APP_DIR): run_command(f"sudo rm -rf {APP_DIR}")
    if os.path.exists(DB_FILE): run_command(f"sudo rm {DB_FILE}")
    if os.path.exists(CLIENT_IP_FILE): run_command(f"sudo rm {CLIENT_IP_FILE}")
    
    print_success("\n--- Uninstallation complete. ---")

def view_service_status():
    print_header("A.N.U.S. Service Status")
    run_command(f"sudo systemctl status {SERVICE_NAME}.service --no-pager")

def view_logs(tail=False):
    command = f"sudo journalctl -u {SERVICE_NAME}.service -n 50 --no-pager"
    if tail:
        command = f"sudo journalctl -u {SERVICE_NAME}.service -f"
        print_header("Tailing Logs for A.N.U.S. Service (Ctrl+C to exit)")
    else:
        print_header("Last 50 Log Entries for A.N.U.S. Service")
    run_command(command)

def _recreate_db_tables():
    """Helper to create empty tables in the database."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DROP TABLE IF EXISTS metrics")
            cursor.execute("DROP TABLE IF EXISTS resource_metrics")
            cursor.execute("DROP TABLE IF EXISTS local_network_info")
            cursor.execute("DROP TABLE IF EXISTS nmap_scan_results")
            cursor.execute("DROP TABLE IF EXISTS client_pings")
            cursor.execute("""
                CREATE TABLE metrics (
                    id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL,
                    status TEXT, dns_info TEXT, traceroute_info TEXT,
                    timestamp INTEGER, packet_loss REAL
                )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics (name, timestamp DESC)")
            cursor.execute("""
                CREATE TABLE resource_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,
                    cpu_usage REAL, mem_usage REAL, net_down_kbps REAL, net_up_kbps REAL
                )""")
            cursor.execute("""
                CREATE TABLE local_network_info (
                    id INTEGER PRIMARY KEY, gateway_ip TEXT UNIQUE, gateway_mac TEXT,
                    gateway_vendor TEXT, last_updated INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE nmap_scan_results (
                    id INTEGER PRIMARY KEY, ip TEXT UNIQUE, mac_address TEXT,
                    vendor TEXT, hostname TEXT, is_up BOOLEAN,
                    services TEXT, os TEXT, last_scanned INTEGER
                )
            """)
            cursor.execute("CREATE TABLE client_pings (ip TEXT PRIMARY KEY, ping REAL, timestamp INTEGER)")
            conn.commit()
        set_permissions() # Reset permissions on the new DB file
        print_success("Database tables recreated.")
        return True
    except Exception as e:
        print_error(f"Failed to recreate database tables: {e}")
        return False

def clear_database():
    """Wipes and recreates the metrics database."""
    print_header("Clearing Database")
    confirm = input(f"{colors.YELLOW}Are you sure you want to delete all metrics? This cannot be undone. (y/N): {colors.ENDC}")
    if confirm.lower() != 'y':
        print_info("Database clear cancelled.")
        return

    print_info("Stopping service...")
    run_command(f"sudo systemctl stop {SERVICE_NAME}.service", ignore_errors=True)
    print_info("Deleting database file...")
    if os.path.exists(DB_FILE):
        if not run_command(f"sudo rm {DB_FILE}"):
            print_error("Failed to delete database file. Aborting.")
            run_command(f"sudo systemctl start {SERVICE_NAME}.service", ignore_errors=True)
            return
    
    print_info("Recreating database...")
    if _recreate_db_tables():
        print_success("Database cleared successfully.")
    else:
        print_error("Failed to clear database.")
    
    print_info("Restarting service...")
    run_command(f"sudo systemctl start {SERVICE_NAME}.service", ignore_errors=True)

def view_database_stats():
    """Shows statistics from the database."""
    print_header("Database Statistics")
    if not os.path.exists(DB_FILE):
        print_warning("Database file not found. Please install the application first.")
        return
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM metrics")
            metrics_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM resource_metrics")
            resources_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM nmap_scan_results")
            nmap_count = cursor.fetchone()[0]
            cursor.execute("SELECT name, COUNT(*) FROM metrics GROUP BY name ORDER BY name")
            targets = cursor.fetchall()
            cursor.execute("SELECT gateway_ip, gateway_mac, gateway_vendor FROM local_network_info LIMIT 1")
            gateway_info = cursor.fetchone()

        db_size = os.path.getsize(DB_FILE) / (1024*1024) # Size in MB

        print(f"{colors.BOLD}A.N.U.S. {VERSION} Database Statistics{colors.ENDC}")
        print(f"----------------------------------------")
        if gateway_info:
            print(f"{colors.BOLD}Gateway Info (Cached):{colors.ENDC}")
            print(f"  - IP: {gateway_info[0]}")
            print(f"  - MAC: {gateway_info[1]}")
            print(f"  - Vendor: {gateway_info[2]}")
            print(f"----------------------------------------")
        print(f"{colors.BOLD}Total Ping Records:{colors.ENDC} {metrics_count}")
        print(f"{colors.BOLD}Total Resource Records:{colors.ENDC} {resources_count}")
        print(f"{colors.BOLD}Total Nmap Scan Records:{colors.ENDC} {nmap_count}")
        print(f"{colors.BOLD}Database File Size:{colors.ENDC} {db_size:.2f} MB")
        print(f"{colors.BOLD}Records per Target:{colors.ENDC}")
        for target, count in targets:
            print(f"  - {target}: {count}")

    except Exception as e:
        print_error(f"Could not read database stats: {e}")

def show_menu():
    """Displays the interactive menu."""
    while True:
        print_header(f"A.N.U.S. Setup Menu ({VERSION})")
        print(f"{colors.GREEN}1.{colors.ENDC} Install / Update")
        print(f"{colors.YELLOW}2.{colors.ENDC} View Service Status")
        print(f"{colors.YELLOW}3.{colors.ENDC} View Service Logs (Last 50)")
        print(f"{colors.YELLOW}4.{colors.ENDC} Tail Service Logs (Live)")
        print(f"{colors.YELLOW}5.{colors.ENDC} View Database Stats")
        print(f"{colors.RED}6.{colors.ENDC} Clear Database")
        print(f"{colors.RED}7.{colors.ENDC} Uninstall")
        print(f"-----------------------------")
        print(f"{colors.BLUE}0.{colors.ENDC} Exit")
        
        choice = input("Enter your choice: ")
        
        if choice == '1':
            install_or_update()
        elif choice == '2':
            view_service_status()
        elif choice == '3':
            view_logs(tail=False)
        elif choice == '4':
            view_logs(tail=True)
        elif choice == '5':
            view_database_stats()
        elif choice == '6':
            clear_database()
        elif choice == '7':
            uninstall()
        elif choice == '0':
            print_info("Exiting.")
            break
        else:
            print_error("Invalid choice, please try again.")
        
        input("\nPress Enter to return to the menu...")
        os.system('clear')

def main():
    """Main execution function."""
    if os.geteuid() != 0:
        print_error("This script must be run as root. Please use sudo.")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1].lower() in ['-menu', '/menu', '--menu']:
        show_menu()
    else:
        install_or_update()

if __name__ == "__main__":
    main()
