#!/usr/bin/python3
# A.N.U.S. v1.4.0 - Curses UI Module

import curses
import textwrap
import subprocess
import threading
from datetime import datetime
import json
import os
import re
import sys
import urllib.request
import hashlib

# This is a helper module for setup_anus_app.py.
# It contains all the logic for the interactive curses-based UI.

# --- Version & File Metadata Logic ---

def get_version_from_content(content):
    """Extracts a vX.Y.Z version string from content using multiple, flexible patterns."""
    patterns = [
        re.compile(r'# A\.N\.U\.S\. v(\d+\.\d+\.\d+)'),
        re.compile(r'// A\.N\.U\.S\. v(\d+\.\d+\.\d+)'),
        re.compile(r'/\* A\.N\.U\.S\. v(\d+\.\d+\.\d+)'),
        re.compile(r'const APP_VERSION = \'\s*v?(\d+\.\d+\.\d+)\s*\''),
        re.compile(r'"_version":\s*"\s*v?(\d+\.\d+\.\d+)\s*"'),
        re.compile(r'badge/Version-v?(\d+\.\d+\.\d+)-blue\.svg'), # More specific badge URL
        re.compile(r'/releases/tag/v(\d+\.\d+\.\d+)'),
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return f"v{match.group(1)}"
    return "N/A"

def get_local_file_meta(file_path):
    """Returns the version, sha256 hash, and mtime for a local file."""
    if not os.path.exists(file_path):
        return "N/A", "N/A", "N/A"
    try:
        with open(file_path, 'rb') as f:
            content_bytes = f.read()
            sha256_hash = hashlib.sha256(content_bytes).hexdigest()
            content_str = content_bytes.decode('utf-8', errors='ignore')
            version = get_version_from_content(content_str)
        mtime = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M')
        return version, sha256_hash, mtime
    except Exception:
        return "Error", "Error", "Error"

def get_github_file_meta(repo, path):
    """Returns the version, sha256 hash, and commit date from GitHub."""
    try:
        # Get file content and calculate SHA256 hash
        content_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
        req = urllib.request.Request(content_url, headers={'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content_bytes = response.read()
            github_hash = hashlib.sha256(content_bytes).hexdigest()
            content_str = content_bytes.decode('utf-8', errors='ignore')
            version = get_version_from_content(content_str)

        # Get last commit date for the file
        commit_url = f"https://api.github.com/repos/{repo}/commits?path={path}&page=1&per_page=1"
        req = urllib.request.Request(commit_url, headers={'Accept': 'application/vnd.github.v3+json', 'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(req, timeout=10) as response:
            commit_data = json.load(response)
            commit_date_str = commit_data[0]['commit']['committer']['date']
            commit_date = datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ").strftime('%Y-%m-%d %H:%M')
        
        return version, github_hash, commit_date
    except Exception:
        return "Error", "Error", "Error"


def get_version_comparison_data():
    """Fetches comprehensive version and metadata for all relevant files."""
    from setup_anus_app import APP_DIR, ASSETS_DIR, GITHUB_REPO

    files_to_check = {
        "setup_anus_app.py": ("setup_anus_app.py", "---"),
        "setup_anus_curses_ui.py": ("setup_anus_curses_ui.py", "---"),
        "anus_service.py": ("anus_service.py", os.path.join(APP_DIR, "anus_service.py")),
        "index.html": ("index.html", os.path.join(APP_DIR, "index.html")),
        "ping.php": ("ping.php", os.path.join(APP_DIR, "ping.php")),
        "assets/style.css": ("assets/style.css", os.path.join(ASSETS_DIR, "style.css")),
        "assets/themes.json": ("assets/themes.json", os.path.join(ASSETS_DIR, "themes.json")),
        "assets/fuzzy_sayings.json": ("assets/fuzzy_sayings.json", os.path.join(ASSETS_DIR, "fuzzy_sayings.json")),
        "README.md": ("README.md", os.path.join(APP_DIR, "../README.md")),
    }
    
    comparison_data = []
    local_installer_dir = os.path.dirname(os.path.realpath(sys.argv[0]))

    for file_name, (local_rel_path, installed_path) in files_to_check.items():
        row = {"file": file_name}
        
        # Get Local Installer Info
        local_file_path = os.path.join(local_installer_dir, local_rel_path)
        row["local_ver"], row["local_hash"], row["local_date"] = get_local_file_meta(local_file_path)

        # Get Installed Info
        if installed_path == "---":
            row["installed_ver"], row["installed_hash"], row["installed_date"] = "---", "---", "---"
        else:
            row["installed_ver"], row["installed_hash"], row["installed_date"] = get_local_file_meta(installed_path)

        # Get GitHub Info
        row["github_ver"], row["github_hash"], row["github_date"] = get_github_file_meta(GITHUB_REPO, local_rel_path)
        
        comparison_data.append(row)
    return comparison_data

class CursesUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.menu = ["Install / Update", "Check for Updates", "Update from GitHub", "Re-install", "View Service Status", "View Service Logs", "Clear Output", "Clear Database", "Uninstall", "Exit"]
        self.current_row = 0
        self.init_curses()

    def init_curses(self):
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()

            # Define colors that are safe for 8-color terminals
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_CYAN, -1)
            curses.init_pair(5, curses.COLOR_GREEN, -1)
            curses.init_pair(6, curses.COLOR_MAGENTA, -1)
            curses.init_pair(7, curses.COLOR_WHITE, -1)
            curses.init_pair(8, curses.COLOR_BLUE, -1)

            # Adaptively define the 'dim' color if supported
            if curses.COLORS >= 16:
                curses.init_pair(9, 8, -1)  # Use bright black (grey)
            else:
                curses.init_pair(9, curses.COLOR_WHITE, -1) # Fallback

    def draw_layout(self, menu_visible=True):
        h, w = self.stdscr.getmaxyx()
        self.stdscr.bkgd(' ', curses.color_pair(7)); self.stdscr.clear()
        from setup_anus_app import VERSION
        header = f"A.N.U.S. Setup Utility ({VERSION})"
        footer = "Use ↑/↓ to navigate, Enter to select, Q to quit." if menu_visible else "Installation in progress..."
        self.stdscr.addstr(0, w//2 - len(header)//2, header, curses.color_pair(6)|curses.A_BOLD)
        self.stdscr.addstr(h-1, w//2 - len(footer)//2, footer, curses.color_pair(6)|curses.A_BOLD)

        if menu_visible:
            menu_w, status_h = 30, 9
            self.menu_win = curses.newwin(h-2, menu_w, 1, 1)
            self.output_win = curses.newwin(h-2-status_h, w-menu_w-2, 1, menu_w+1)
            self.status_win = curses.newwin(status_h, w-menu_w-2, h-1-status_h, menu_w+1)
            self.menu_win.box(); self.status_win.box()
            self.menu_win.addstr(0, 2, " Menu ", curses.color_pair(8)|curses.A_BOLD)
            self.status_win.addstr(0, 2, " System Info ", curses.color_pair(8)|curses.A_BOLD)
        else: self.output_win = curses.newwin(h-2, w-2, 1, 1)
        self.progress_win = curses.newwin(3, w-2, h-4, 1)
        self.output_win.box(); self.output_win.addstr(0, 2, " Output ", curses.color_pair(8)|curses.A_BOLD)
        self.output_win.scrollok(True); self.stdscr.refresh()
        if menu_visible: self.menu_win.refresh(); self.status_win.refresh()
        self.output_win.refresh()

    def update_status_panel(self):
        from setup_anus_app import detect_ssl_certs, get_ssl_cert_details
        self.status_win.clear(); self.status_win.box(); self.status_win.addstr(0, 2, " System Info ", curses.color_pair(8)|curses.A_BOLD)
        hostname, cert_file, _ = detect_ssl_certs(lambda m: None, lambda m: None)
        self.status_win.addstr(1, 2, f"Hostname: {hostname}", curses.color_pair(7))
        cert_details = get_ssl_cert_details(cert_file)
        if cert_details:
            self.status_win.addstr(2, 2, "SSL Cert:", curses.color_pair(5))
            self.status_win.addstr(3, 4, f"Issuer: {cert_details['issuer']}", curses.color_pair(7))
            days_left = (datetime.strptime(cert_details['end_date'], '%Y-%m-%d %H:%M:%S') - datetime.now()).days
            color = curses.color_pair(2) if days_left<7 else curses.color_pair(3) if days_left<14 else curses.color_pair(5)
            self.status_win.addstr(4, 4, f"Expires: {cert_details['end_date']} ({days_left} days left)", color)
        else: self.status_win.addstr(2, 2, "SSL Cert: Not Found", curses.color_pair(2))
        self.status_win.refresh()
        
    def draw_menu(self):
        h, w = self.menu_win.getmaxyx(); self.menu_win.clear(); self.menu_win.box()
        self.menu_win.addstr(0, 2, " Menu ", curses.color_pair(8)|curses.A_BOLD)
        colors = {"Install / Update":5, "Check for Updates":4, "Update from GitHub":3, "Re-install":3, "View Service Status":4, "View Service Logs":4, "Clear Output":7, "Clear Database":2, "Uninstall":2, "Exit":7}
        for i, r in enumerate(self.menu):
            y, x = h//2 - len(self.menu)//2 + i, w//2 - len(r)//2
            self.menu_win.addstr(y, x, f" {r} " if i==self.current_row else r, curses.color_pair(1 if i==self.current_row else colors.get(r,7)))
        self.menu_win.refresh()

    def update_progress_bar(self, current, total, msg=""):
        h, w = self.progress_win.getmaxyx(); self.progress_win.clear(); self.progress_win.box()
        percent = int((current/total)*100); bar_w = w-8; filled = int(bar_w*percent/100)
        bar = '█'*filled + '─'*(bar_w-filled)
        self.progress_win.addstr(1, 2, f"[{bar}] {percent}%", curses.color_pair(5))
        self.progress_win.addstr(0, 2, f" Progress: {msg[:bar_w-10]} ", curses.color_pair(8)|curses.A_BOLD)
        self.progress_win.refresh()
        
    def display_version_comparison(self, win, data):
        win.clear(); win.box()
        win.addstr(0, 2, " Version & Integrity Check (Scrollable ↑↓) ", curses.color_pair(8) | curses.A_BOLD)
        win.refresh()
        
        max_y, max_x = win.getmaxyx()
        pad_height = len(data) * 6 + 4
        pad = curses.newpad(pad_height, max_x - 2)
        pad.keypad(True)

        is_gh_newer, is_local_newer = False, False
        y = 1
        
        for r in data:
            if y > 1: pad.addstr(y, 1, "─" * (max_x - 4), curses.color_pair(9) | curses.A_DIM); y += 1
            
            display_filename = r['file']
            available_width = max_x - 5
            if len(display_filename) > available_width:
                display_filename = display_filename[:available_width - 1] + "…"

            pad.addstr(y, 2, display_filename, curses.A_BOLD | curses.color_pair(4)); y += 1
            pad.addstr(y, 4, f"{'':<12}{'Version':<12}{'Date':<20}{'Hash (SHA256)':<18}", curses.A_UNDERLINE | curses.color_pair(7)); y += 1

            try: local_v = tuple(map(int, r['local_ver'].strip('v').split('.'))) if r['local_ver'] != "N/A" else (0,0,0)
            except: local_v = (0,0,0)
            try: gh_v = tuple(map(int, r['github_ver'].strip('v').split('.'))) if r['github_ver'] not in ["N/A", "Error"] else (0,0,0)
            except: gh_v = (0,0,0)

            if gh_v > local_v: is_gh_newer = True
            if local_v > gh_v and gh_v != (0,0,0): is_local_newer = True

            locations = ["Installed", "Local", "GitHub"]
            versions = [r['installed_ver'], r['local_ver'], r['github_ver']]
            dates = [r['installed_date'], r['local_date'], r['github_date']]
            hashes = [r['installed_hash'], r['local_hash'], r['github_hash']]
            
            for i, loc in enumerate(locations):
                v_color = curses.color_pair(7)
                if versions[i] != "---":
                    try: current_v_tuple = tuple(map(int, versions[i].strip('v').split('.'))) if versions[i] not in ["N/A", "Error"] else (0,0,0)
                    except: current_v_tuple = (0,0,0)
                    if gh_v > current_v_tuple: v_color = curses.color_pair(2)
                    elif current_v_tuple > gh_v and gh_v != (0,0,0): v_color = curses.color_pair(3)
                
                hash_color = curses.color_pair(7)
                if hashes[i] not in ["N/A", "---", "Error"] and r['github_hash'] not in ["N/A", "Error"]:
                    hash_color = curses.color_pair(5) if hashes[i] == r['github_hash'] else curses.color_pair(2)

                pad.addstr(y, 4, f"{loc:<12}"); pad.addstr(f"{versions[i]:<12}", v_color)
                pad.addstr(f"{dates[i]:<20}", curses.color_pair(9) | curses.A_DIM); pad.addstr(f"{hashes[i][:16]:<18}", hash_color); y += 1
        
        # Summary Status in a footer window
        footer_win = win.derwin(4, max_x - 2, max_y - 4, 1)
        footer_win.addstr(1, 1, "Status:", curses.A_BOLD | curses.color_pair(7))
        if is_gh_newer:
            footer_win.addstr(1, 9, "A new version is available from GitHub.", curses.color_pair(5) | curses.A_BOLD)
            footer_win.addstr(2, 1, "Select 'Update from GitHub' to install the latest version.", curses.color_pair(7))
        elif is_local_newer:
            footer_win.addstr(1, 9, "You have a newer local version than GitHub.", curses.color_pair(3) | curses.A_BOLD)
            footer_win.addstr(2, 1, "Living on the edge, I see! Be careful with that.", curses.color_pair(7))
        elif any(r['github_ver'] == "Error" for r in data):
            footer_win.addstr(1, 9, "Could not fetch all versions from GitHub.", curses.color_pair(2) | curses.A_BOLD)
        else:
            footer_win.addstr(1, 9, "Your local installer is up-to-date with GitHub.", curses.color_pair(5) | curses.A_BOLD)
        footer_win.refresh()

        # Scrolling loop
        scroll_pos = 0
        pad_max_scroll = max(0, pad_height - (max_y - 5))
        while True:
            pad.refresh(scroll_pos, 0, 1, 1, max_y - 5, max_x - 2)
            key = win.getch()
            if key == curses.KEY_UP and scroll_pos > 0: scroll_pos -= 1
            elif key == curses.KEY_DOWN and scroll_pos < pad_max_scroll: scroll_pos += 1
            elif key in [ord('q'), curses.KEY_ENTER, 10, 13]: break


    def run_command_curses(self, win, command, ignore_errors=False, show_output=True):
        from setup_anus_app import VERBOSE_MODE
        # The 'show_output' argument is now accepted, but its behavior is controlled by VERBOSE_MODE
        if VERBOSE_MODE: 
            win.addstr(f"\n$ {command}\n", curses.color_pair(4)|curses.A_BOLD); win.refresh()
        try:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
            out_l, err_l = [],[]
            def read_stream(s, l, c):
                for line in iter(s.readline, ''):
                    l.append(line)
                    if VERBOSE_MODE:
                        for w_line in textwrap.wrap(line.strip(), win.getmaxyx()[1]-4): win.addstr(f"  {w_line}\n",c); win.refresh()
            out_t = threading.Thread(target=read_stream, args=(proc.stdout, out_l, curses.color_pair(7)))
            err_t = threading.Thread(target=read_stream, args=(proc.stderr, err_l, curses.color_pair(3)))
            out_t.start(); err_t.start(); out_t.join(); err_t.join()
            rc = proc.wait()
            if rc != 0 and not ignore_errors:
                win.addstr(f"\nError: Command failed (code {rc})\n", curses.color_pair(2)|curses.A_BOLD)
                if not VERBOSE_MODE:
                    for n, l, c in [("STDOUT",out_l,7), ("STDERR",err_l,2)]:
                        if l:
                            win.addstr(f"--- {n} ---\n", curses.color_pair(2))
                            for line in l: win.addstr(f"  {line.strip()}\n", curses.color_pair(c))
                win.refresh(); return False
            return True
        except Exception as e: win.addstr(f"\nFATAL ERROR: {e}\n", curses.color_pair(2)|curses.A_BOLD); win.refresh(); return False

    def run(self):
        from setup_anus_app import install_prerequisites, setup_directories_and_files, download_assets, copy_files, set_permissions, configure_apache, apply_system_tweaks, setup_database, setup_systemd_service, SERVICE_NAME, VERSION
        self.draw_layout(); self.update_status_panel(); self.draw_menu()
        action_on_exit = None
        try:
            while True:
                key = self.stdscr.getch()
                if key in [curses.KEY_UP, curses.KEY_DOWN]: self.current_row = (self.current_row + (1 if key==curses.KEY_DOWN else -1)) % len(self.menu)
                elif key == ord('q'): break
                elif key in [curses.KEY_ENTER, 10, 13]:
                    action = self.menu[self.current_row]
                    if action == "Exit": break
                    if action == "Clear Output": self.output_win.clear(); self.output_win.box(); self.output_win.addstr(0,2," Output ",curses.color_pair(8)|curses.A_BOLD); self.output_win.refresh(); continue
                    if action in ["Uninstall","Clear Database","Re-install","Update from GitHub"]: action_on_exit = action.lower().replace("-","_").replace(" ","_"); break
                    self.output_win.clear(); self.output_win.box(); self.output_win.addstr(0,2,f" {action} ", curses.color_pair(8)|curses.A_BOLD); self.output_win.refresh()
                    def c_s(m): self.output_win.addstr(f"✔ {m}\n", curses.color_pair(5)); self.output_win.refresh()
                    def c_e(m): self.output_win.addstr(f"✖ {m}\n", curses.color_pair(2)); self.output_win.refresh()
                    def c_i(m): self.output_win.addstr(f"{m}\n", curses.color_pair(7)); self.output_win.refresh()
                    def c_w(m): self.output_win.addstr(f"⚠ {m}\n", curses.color_pair(3)); self.output_win.refresh()
                    def c_r(cmd,ign=False, show_output=True): return self.run_command_curses(self.output_win, cmd, ign, show_output)

                    if action == "Install / Update":
                        steps = [("Prerequisites", install_prerequisites), ("Directories", setup_directories_and_files), ("Assets", download_assets), ("Copying Files", copy_files), ("Permissions", set_permissions), ("Apache", configure_apache), ("System Tweaks", apply_system_tweaks), ("Database", setup_database), ("Service", setup_systemd_service), ("Finalizing", lambda r,i,s,e,w: r("sudo systemctl restart apache2", True))]
                        for i, (msg, func) in enumerate(steps):
                            self.update_progress_bar(i, len(steps), msg); c_i(f"\n--- {msg} ---")
                            if not func(c_r, c_i, c_s, c_e, c_w): c_e(f"\n--- Step '{msg}' failed. ---"); break
                        else: self.update_progress_bar(len(steps), len(steps), "Complete!"); c_s(f"\n--- A.N.U.S. {VERSION} Install/Update Complete! ---")
                    elif action == "Check for Updates":
                        c_i("Fetching version information from GitHub API..."); self.output_win.refresh()
                        self.display_version_comparison(self.output_win, get_version_comparison_data())
                    elif action == "View Service Status":
                        c_r(f"sudo systemctl status {SERVICE_NAME}.service --no-pager")
                    elif action == "View Service Logs":
                        c_r(f"sudo journalctl -u {SERVICE_NAME}.service -n 50 --no-pager")

                    # This part is now handled inside display_version_comparison
                    if action != "Check for Updates":
                         self.output_win.addstr("\n\nPress any key to return.", curses.color_pair(3) | curses.A_BOLD); self.output_win.getch()
                    
                    self.draw_layout(); self.update_status_panel(); self.draw_menu()
                self.draw_menu()
        except KeyboardInterrupt: pass
        return action_on_exit

