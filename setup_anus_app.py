#!/usr/bin/python3
# A.N.U.S. v1.4.0 - Installer Update

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
import urllib.request

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
VERSION = "v1.4.0"
GITHUB_REPO = "sworrl/ANUS"
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
    HEADER = '\033[95m'; BLUE = '\033[94m'; GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; ENDC = '\033[0m'; BOLD = '\033[1m'

# --- Simple Text-Based Functions ---
def clear_screen(): os.system('cls' if os.name == 'nt' else 'clear')
def simple_print_header(message): print(f"\n{colors.BOLD}{colors.HEADER}--- {message} ---{colors.ENDC}")
def simple_print_success(message): print(f"{colors.GREEN}✔ {message}{colors.ENDC}")
def simple_print_warning(message): print(f"{colors.YELLOW}⚠ {message}{colors.ENDC}", file=sys.stderr)
def simple_print_error(message): print(f"{colors.RED}✖ {message}{colors.ENDC}", file=sys.stderr)
def simple_print_info(message): print(f"{colors.BLUE}{message}{colors.ENDC}")

def simple_run_command(command, ignore_errors=False, show_output=True):
    if VERBOSE_MODE: simple_print_info(f"Executing: {command}")
    try:
        if show_output: subprocess.run(command, shell=True, check=True)
        else: subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        if ignore_errors: return True
        simple_print_error(f"Error executing command: {command}\nReturn Code: {e.returncode}")
        if not show_output and hasattr(e, 'stdout') and e.stdout: simple_print_error(f"Output:\n{e.stdout}")
        if not show_output and hasattr(e, 'stderr') and e.stderr: simple_print_error(f"Error Output:\n{e.stderr}")
        return False
    except KeyboardInterrupt: simple_print_warning("\nCommand interrupted by user."); raise

# --- Placeholder Functions for CLI flags ---
def show_help():
    simple_print_header("A.N.U.S. Installer Help")
    print("Usage: sudo python3 setup_anus_app.py [flag]")
    print("Flags:")
    print("  -menu            : Launch the interactive curses menu.")
    print("  -uninstall       : Uninstall the application.")
    print("  -reinstall       : Uninstall and then reinstall the application.")
    print("  -clear-database  : Clear the application database.")
    print("  -status          : Check the status of the background service.")
    print("  -logs            : View the latest logs from the background service.")
    print("  -check-updates   : Check GitHub for new application versions.")
    print("  -update, -github : Pull the latest version from GitHub and run the installer.")
    print("  -verbose         : Show detailed command output during installation.")
    print("  (no flags)       : Run a standard installation or update.")

def simple_uninstall(confirm=True):
    simple_print_header("Uninstalling A.N.U.S.")
    if confirm:
        simple_print_warning("This will permanently remove A.N.U.S., its configurations, and all data.")
        if input("Are you sure you want to continue? (y/N): ").lower() != 'y':
            simple_print_info("Uninstall cancelled."); return
    simple_print_info("Stopping and disabling services..."); simple_run_command(f"sudo systemctl stop {SERVICE_NAME}", True, False); simple_run_command(f"sudo systemctl disable {SERVICE_NAME}", True, False)
    simple_print_info("Removing systemd service file..."); simple_run_command(f"sudo rm {SYSTEMD_SERVICE_PATH}", True); simple_run_command("sudo systemctl daemon-reload", show_output=False)
    simple_print_info("Disabling Apache site..."); simple_run_command(f"sudo a2dissite anus.conf", True, False); simple_run_command(f"sudo rm {APACHE_CONF_PATH}", True); simple_run_command("sudo systemctl reload apache2", show_output=False)
    simple_print_info("Removing application and data directories..."); simple_run_command(f"sudo rm -rf {APP_DIR}", True); simple_run_command(f"sudo rm -rf {DB_DIR}", True)
    simple_print_info("Removing sudoers permission file..."); simple_run_command(f"sudo rm {SUDOERS_FILE}", True)
    simple_print_success("\nA.N.U.S. has been successfully uninstalled.")

def simple_clear_database():
    simple_print_header("Clearing A.N.U.S. Database")
    simple_print_warning("This will permanently delete all collected metrics and data.")
    if input("Are you sure? (y/N): ").lower() != 'y': simple_print_info("Cancelled."); return
    simple_print_info("Stopping service..."); simple_run_command(f"sudo systemctl stop {SERVICE_NAME}", True, False)
    simple_print_info("Deleting database file..."); simple_run_command(f"sudo rm {DB_FILE}", True)
    simple_print_info("Re-initializing database schema...")
    if setup_database(simple_run_command, simple_print_info, simple_print_success, simple_print_error, simple_print_warning): simple_print_success("Database schema re-initialized.")
    else: simple_print_error("Failed to re-initialize database.")
    simple_print_info("Restarting service..."); simple_run_command(f"sudo systemctl start {SERVICE_NAME}", show_output=False)
    simple_print_success("\nDatabase cleared and service is running.")

# --- Core Logic Functions ---
def get_user(): return os.getenv("SUDO_USER", os.getenv("USER"))
def get_file_version(filepath):
    if not os.path.exists(filepath): return None
    try:
        with open(filepath, 'r') as f:
            match = re.search(r'v(\d+\.\d+\.\d+)', f.readline())
            if match: return f"v{match.group(1)}"
    except Exception: return None
    return None

def get_fqdn():
    try: return subprocess.check_output(['hostname', '-f'], text=True).strip() or socket.getfqdn()
    except (subprocess.CalledProcessError, FileNotFoundError): return socket.getfqdn()

def detect_ssl_certs(print_info_func, print_warning_func):
    fqdn, letsencrypt_dir = get_fqdn(), "/etc/letsencrypt/live"
    print_info_func(f"Detected FQDN: {fqdn}")
    if not os.path.isdir(letsencrypt_dir): return fqdn, None, None
    potential_path = os.path.join(letsencrypt_dir, fqdn)
    if os.path.isdir(potential_path) and os.path.exists(os.path.join(potential_path, "fullchain.pem")): return fqdn, os.path.join(potential_path, "fullchain.pem"), os.path.join(potential_path, "privkey.pem")
    subdirs = [d for d in os.listdir(letsencrypt_dir) if os.path.isdir(os.path.join(letsencrypt_dir, d))]
    if subdirs:
        first_dir = subdirs[0]
        print_warning_func(f"No exact SSL match found. Falling back to first available: {first_dir}")
        return first_dir, os.path.join(letsencrypt_dir, first_dir, "fullchain.pem"), os.path.join(letsencrypt_dir, first_dir, "privkey.pem")
    return fqdn, None, None

def get_ssl_cert_details(cert_path):
    if not cert_path or not os.path.exists(cert_path): return None
    try:
        details = {}
        issuer_line = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -issuer", shell=True, text=True).strip()
        details['issuer'] = re.search(r'CN\s?=\s?([^,]+)', issuer_line).group(1) if re.search(r'CN\s?=\s?([^,]+)', issuer_line) else "Unknown"
        for key, cmd in [('start_date', 'startdate'), ('end_date', 'enddate')]:
            date_str = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -{cmd}", shell=True, text=True).split('=')[1].strip()
            details[key] = datetime.strptime(date_str, '%b %d %H:%M:%S %Y %Z').strftime('%Y-%m-%d %H:%M:%S')
        details['fingerprint'] = subprocess.check_output(f"openssl x509 -in {cert_path} -noout -fingerprint -sha256", shell=True, text=True).split('=')[1].strip()
        return details
    except Exception: return None

def setup_database(run, info, success, error, warn):
    info("Setting up/updating database schema...")
    try:
        if not os.path.exists(DB_DIR): os.makedirs(DB_DIR); run(f"sudo chown www-data:www-data {DB_DIR}", False); run(f"sudo chmod 775 {DB_DIR}", False)
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, name TEXT, ping REAL, jitter REAL, status TEXT, dns_info TEXT, traceroute_info TEXT, timestamp INTEGER, packet_loss REAL, is_on_demand BOOLEAN DEFAULT 0, min_ping_15m REAL, max_ping_15m REAL, packet_loss_15m REAL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name_timestamp ON metrics (name, timestamp DESC)")
            c.execute("CREATE TABLE IF NOT EXISTS resource_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL, cpu_usage REAL, mem_usage REAL, net_down_kbps REAL, net_up_kbps REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS local_network_info (id INTEGER PRIMARY KEY, gateway_ip TEXT UNIQUE, gateway_mac TEXT, gateway_vendor TEXT, last_updated INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS nmap_scan_results (id INTEGER PRIMARY KEY, ip TEXT UNIQUE, mac_address TEXT, vendor TEXT, hostname TEXT, is_up BOOLEAN, services TEXT, os TEXT, last_scanned INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, startTime INTEGER NOT NULL, endTime INTEGER)")
            conn.commit()
        run(f"sudo chown www-data:www-data {DB_FILE}", False); run(f"sudo chmod 664 {DB_FILE}", False)
        success("Database setup complete."); return True
    except Exception as e: error(f"Failed to create/update database schema: {e}"); return False

# --- Installation Step Functions (UI Agnostic) ---
def install_prerequisites(run, info, success, error, warn):
    info("Installing prerequisites...")
    packages = ["python3", "python3-pip", "apache2", "php", "libapache2-mod-php", "php-sqlite3", "traceroute", "dnsutils", "iproute2", "wget", "sqlite3", "nmap", "libcap2-bin", "iputils-ping", "git"]
    if not run("sudo apt-get update") or not run(f"sudo apt-get install -y {' '.join(packages)}"):
        error("Failed to install prerequisites."); return False
    success("Prerequisites installed successfully."); return True

def download_assets(run, info, error, success, warn):
    info("Downloading self-hosted assets..."); run(f"sudo mkdir -p {ASSETS_DIR}", show_output=False)
    for filename, url in ASSETS.items():
        local_path = os.path.join(ASSETS_DIR, filename)
        if not os.path.exists(local_path):
            info(f"  Downloading {filename}...")
            ua_flag = "-U 'Mozilla/5.0...' " if "googleapis" in url else ""
            if not run(f"sudo wget -q -o /dev/null {ua_flag}-O {local_path} '{url}'", show_output=False):
                error(f"Failed to download {filename}."); return False
    success("All assets downloaded successfully."); return True

def setup_directories_and_files(run, info, success, error, warn):
    info("Setting up directories..."); run(f"sudo mkdir -p {APP_DIR}", False); run(f"sudo mkdir -p {ASSETS_DIR}", False); run(f"sudo mkdir -p {DB_DIR}", False)
    if not os.path.exists(CLIENT_IP_FILE): run(f"sudo touch {CLIENT_IP_FILE}", False)
    if not os.path.exists(DB_FILE): run(f"sudo touch {DB_FILE}", False)
    if not os.path.exists(os.path.join(ASSETS_DIR, 'style.css')): run(f"sudo touch {os.path.join(ASSETS_DIR, 'style.css')}", False)
    success("Directories and files created."); return True

def copy_files(run, info, warn, error, success):
    info("Copying application files..."); current_dir = os.path.dirname(os.path.realpath(__file__))
    app_files = ["index.html", "ping.php", PYTHON_SERVICE_FILE, "setup_anus_curses_ui.py"]
    web_root_files = ["README.md", "LICENSE"]
    asset_files = ["bruh.mp3", "up.mp3", "targets.json", "fuzzy_sayings.json", "themes.json", "animated-logo.svg", "anus.conf", "style.css"]
    info("  Checking versions of existing files...")
    for file_name in app_files:
        installed_path = os.path.join(APP_DIR, file_name)
        installed_version = get_file_version(installed_path)
        if installed_version:
            if installed_version == VERSION: info(f"  - {file_name} is up to date ({VERSION}).")
            else: warn(f"  - {file_name} will be upgraded from {installed_version} to {VERSION}.")
        else: info(f"  - {file_name} will be installed ({VERSION}).")
    for f_list, dest_dir in [(app_files, APP_DIR), (web_root_files, os.path.join(APP_DIR, "..")), (asset_files, ASSETS_DIR)]:
        for file_name in f_list:
            source_path = os.path.join(current_dir, "assets" if dest_dir == ASSETS_DIR else "", file_name)
            if not os.path.exists(source_path): warn(f"File '{file_name}' not found at {source_path}. Skipping."); continue
            if not run(f"sudo cp {source_path} {dest_dir}/", False): return False
    run(f"sudo chmod +x {APP_DIR}/{PYTHON_SERVICE_FILE}", False)
    success("Application files copied."); return True

def set_permissions(run, info, success, error, warn):
    info("Setting final file permissions..."); run(f"sudo chown -R www-data:www-data {DB_DIR}", False); run(f"sudo chmod -R 775 {DB_DIR}", False)
    run(f"sudo chown -R www-data:www-data {APP_DIR}", False); run(f"sudo chmod -R 755 {APP_DIR}", False)
    web_root = os.path.join(APP_DIR, "..")
    for file_name in ["README.md", "LICENSE"]:
        path = os.path.join(web_root, file_name)
        if os.path.exists(path): run(f"sudo chmod 664 {path}", False); run(f"sudo chown www-data:www-data {path}", False)
    if os.path.exists(os.path.join(ASSETS_DIR, "targets.json")): run(f"sudo chmod 664 {ASSETS_DIR}/targets.json", False)
    success("File permissions set correctly."); return True

def configure_apache(run, info, warn, error, success):
    info("Configuring Apache..."); hostname, cert_file, key_file = detect_ssl_certs(info, warn)
    if not cert_file or not key_file: error("Could not find SSL certificate files. Please ensure Let's Encrypt is set up."); return False
    try:
        with open("ssl_info.json.tmp", "w") as f: json.dump({"cert_path": cert_file}, f)
        run(f"sudo cp ssl_info.json.tmp {SSL_INFO_CONFIG_FILE}", False); os.remove("ssl_info.json.tmp")
    except Exception as e: error(f"Failed to write SSL info file: {e}"); return False
    conf = f"""<VirtualHost *:80>\n    ServerName {hostname}\n    Redirect permanent / https://{hostname}/anus/\n</VirtualHost>\n<VirtualHost *:443>\n    ServerName {hostname}\n    DocumentRoot /var/www/html\n    AliasMatch "^/anus(.*)" "{APP_DIR}$1"\n    <Directory "{APP_DIR}">\n        Options -Indexes\n        AllowOverride None\n        Require all granted\n        DirectoryIndex index.html\n    </Directory>\n    <Directory "{DB_DIR}">\n        Require all denied\n    </Directory>\n    SSLEngine on\n    SSLCertificateFile {cert_file}\n    SSLCertificateKeyFile {key_file}\n    ErrorLog ${{APACHE_LOG_DIR}}/error.log\n    CustomLog ${{APACHE_LOG_DIR}}/access.log combined\n</VirtualHost>"""
    try:
        with open("anus.conf.tmp", "w") as f: f.write(conf)
        run(f"sudo cp anus.conf.tmp {APACHE_CONF_PATH}", False); os.remove("anus.conf.tmp")
    except Exception as e: error(f"Failed to write Apache config file: {e}"); return False
    run("sudo a2ensite anus.conf", True); run("sudo a2enmod ssl alias", True)
    success("Apache configured."); return True

def setup_systemd_service(run, info, success, error, warn):
    info(f"Setting up systemd service: {SERVICE_NAME}..."); content = f"[Unit]\nDescription=A.N.U.S. Network Monitoring Service\nAfter=network.target\n\n[Service]\nExecStart=/usr/bin/python3 {APP_DIR}/{PYTHON_SERVICE_FILE}\nWorkingDirectory={APP_DIR}\nStandardOutput=inherit\nStandardError=inherit\nRestart=always\nUser=www-data\n\n[Install]\nWantedBy=multi-user.target"
    with open(f"{SERVICE_NAME}.service.tmp", "w") as f: f.write(content)
    run(f"sudo mv {SERVICE_NAME}.service.tmp {SYSTEMD_SERVICE_PATH}", False); run("sudo systemctl daemon-reload", False)
    run(f"sudo systemctl enable {SERVICE_NAME}.service", False); run(f"sudo systemctl restart {SERVICE_NAME}.service", False)
    success("Systemd service created and started."); return True

def apply_system_tweaks(run, info, success, error, warn):
    info("Applying system tweaks..."); nmap_path, traceroute_path = shutil.which("nmap"), shutil.which("traceroute")
    if not nmap_path or not traceroute_path: error("Could not find 'nmap' or 'traceroute' executables."); return False
    sudoers_content = f"www-data ALL=(ALL) NOPASSWD: {nmap_path}\nwww-data ALL=(ALL) NOPASSWD: {traceroute_path}\n"
    info(f"  Creating sudoers file at {SUDOERS_FILE}...")
    try:
        with open("anus-permissions.tmp", "w") as f: f.write(sudoers_content)
        run(f"sudo mv anus-permissions.tmp {SUDOERS_FILE}", False); run(f"sudo chown root:root {SUDOERS_FILE}", False); run(f"sudo chmod 0440 {SUDOERS_FILE}", False)
        success("Sudoers rule created successfully."); return True
    except Exception as e: error(f"Failed to create sudoers file: {e}"); return False

def check_for_updates(print_info_func, print_success_func, print_warning_func, print_error_func):
    """Checks GitHub for a new version of the application."""
    print_info_func(f"Checking for updates from GitHub: {GITHUB_REPO}...")
    try:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        with urllib.request.urlopen(api_url) as response:
            data = json.loads(response.read().decode())
            latest_version = data.get("tag_name", "v0.0.0").strip('v')
            current_version = VERSION.strip('v')
            print_info_func(f"Current installed version: v{current_version}")
            print_info_func(f"Latest version on GitHub: v{latest_version}")
            if tuple(map(int, latest_version.split('.'))) > tuple(map(int, current_version.split('.'))):
                print_success_func(f"\nUpdate available! Version v{latest_version} is ready.")
                return True
            else:
                print_success_func("\nYou are running the latest version.")
                return False
    except Exception as e:
        print_error_func(f"Could not check for updates. Error: {e}")
        return False

def perform_github_update(run_command_func, print_info_func, print_success_func, print_error_func):
    """Pulls the latest version from GitHub and re-runs the installer."""
    print_info_func("Attempting to update from the 'main' branch...")
    if not os.path.isdir('.git'):
        print_error_func("This does not appear to be a git repository. Cannot update automatically."); return
    if not run_command_func("git fetch origin", show_output=True):
        print_error_func("Failed to fetch updates from the remote repository."); return
    print_info_func("Resetting local repository to match the remote 'main' branch...")
    if not run_command_func("git reset --hard origin/main", show_output=True):
        print_error_func("Failed to reset the local repository. Update aborted."); return
    print_success_func("Update downloaded successfully.")
    print_info_func("The installer will now restart to apply the updates...")
    time.sleep(3)
    os.execvp('sudo', ['sudo', sys.executable] + sys.argv)

# --- Main execution block ---
def main():
    if os.geteuid() != 0: simple_print_error("This script must be run as root. Please use sudo."); sys.exit(1)
    args = {arg.lstrip('-/').lower() for arg in sys.argv[1:]}
    if 'help' in args: show_help(); sys.exit(0)
    if 'uninstall' in args: simple_uninstall(); sys.exit(0)
    if 'reinstall' in args: simple_uninstall(confirm=False); simple_install_or_update_with_funcs(); sys.exit(0)
    if 'clear-database' in args or 'cleardatabase' in args: simple_clear_database(); sys.exit(0)
    if 'status' in args: os.system(f"sudo systemctl status {SERVICE_NAME}.service"); sys.exit(0)
    if 'logs' in args: os.system(f"sudo journalctl -u {SERVICE_NAME}.service -n 50"); sys.exit(0)
    if 'check-updates' in args: check_for_updates(simple_print_info, simple_print_success, simple_print_warning, simple_print_error); sys.exit(0)
    if 'update' in args or 'github' in args:
        simple_print_warning("This will overwrite any local changes with the latest version from GitHub.")
        if input("Are you sure? (y/N): ").lower() == 'y': perform_github_update(simple_run_command, simple_print_info, simple_print_success, simple_print_error)
        else: simple_print_info("Update cancelled.")
        sys.exit(0)

    try:
        simple_install_or_update_with_funcs()
    except KeyboardInterrupt: print("\n\nOperation cancelled by user."); sys.exit(0)

def simple_install_or_update_with_funcs():
    simple_print_header(f"Starting A.N.U.S. {VERSION} Installation/Update")
    steps = [("Installing prerequisites", install_prerequisites), ("Setting up directories", setup_directories_and_files), ("Downloading assets", download_assets), ("Copying application files", copy_files), ("Setting permissions", set_permissions), ("Configuring Apache", configure_apache), ("Applying system tweaks", apply_system_tweaks), ("Setting up database", setup_database), ("Setting up systemd service", setup_systemd_service)]
    for i, (msg, step_func) in enumerate(steps):
        simple_print_header(f"Step {i+1}/{len(steps)}: {msg}")
        if not step_func(simple_run_command, simple_print_info, simple_print_success, simple_print_error, simple_print_warning):
            simple_print_error(f"\n--- Step '{msg}' failed. Aborting installation. ---"); return
        simple_print_success(f"Step '{msg}' complete.")
    simple_print_header("Finalizing Setup"); simple_run_command("sudo systemctl restart apache2"); simple_run_command(f"sudo systemctl status {SERVICE_NAME}.service")
    simple_print_success(f"\n--- A.N.U.S. {VERSION} Installation/Update complete! ---")

if __name__ == "__main__":
    if "-menu" in sys.argv and CURSES_AVAILABLE:
        try:
            from setup_anus_curses_ui import CursesUI
            action_on_exit = curses.wrapper(lambda stdscr: CursesUI(stdscr).run())
            if action_on_exit == "uninstall": simple_uninstall()
            elif action_on_exit == "re_install": simple_uninstall(confirm=False); simple_install_or_update_with_funcs()
            elif action_on_exit == "clear_database": simple_clear_database()
            elif action_on_exit == "update_from_github": perform_github_update(simple_run_command, simple_print_info, simple_print_success, simple_print_error)
        except ImportError:
            simple_print_error("Curses UI module not found. Please ensure 'setup_anus_curses_ui.py' is in the same directory.")
            sys.exit(1)
    elif "-menu" in sys.argv and not CURSES_AVAILABLE:
        simple_print_error("The '-menu' flag was used, but the 'curses' library is not available on this system.")
        simple_print_warning("Please install it (e.g., 'sudo apt-get install libncursesw5-dev') or run the installer without the '-menu' flag.")
    else:
        main()

