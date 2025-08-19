A.N.U.S. - Automated Network Utility System
@version 1.3.6

<p align="center">
<strong>A comprehensive, self-hosted network monitoring dashboard.</strong>
<br><br>
<a href="#-installation">
<img src="https://img.shields.io/badge/install-scripted-brightgreen?style=for-the-badge" alt="Install Script">
</a>
<a href="#-license">
<img src="https://img.shields.io/badge/license-GPLv3-blue?style=for-the-badge" alt="GPLv3 License">
</a>
</p>

A.N.U.S. (Automated Network Utility System) continuously tracks key internet health metrics like latency, jitter, and packet loss for custom targets, while also monitoring host server resources like CPU, memory, and network throughput. It features a sleek, modern web UI with real-time stats and historical trend charts.

🖼️ Screenshots
<div align="center">
<img src="./assets/dashboard-view.png" alt="Main Dashboard View Screenshot" width="700">
<br>
<strong>Main Dashboard View</strong>
<br>
<img src="./assets/trends-view.png" alt="Historical Trends View Screenshot" width="700">
<br>
<strong>Historical Trends View</strong>
<br>
<img src="./assets/calendar-view.png" alt="Event Log Calendar View Screenshot" width="700">
<br>
<strong>Event Log (Calendar View)</strong>
</div>

🚀 Features
Comprehensive Monitoring: Tracks Ping, Jitter, and Packet Loss for multiple user-defined targets.

Real-time Dashboard: A sleek, modern UI provides an at-a-glance overview of your entire network's health, uptime statistics, and quality scores.

Server Health: Monitors the host server's CPU usage, memory usage, and network throughput in real-time.

Interactive Network Neighborhood: A dynamic, map-like visualization of your local network. Discover devices, view their status, and pan/zoom to explore the network topology.

Historical Trends: An interactive chart displays historical latency data, allowing you to zoom and pan through time to identify patterns.

Event Logging: Automatically logs network status changes (UP/DOWN) with duration, viewable in both a timeline and a monthly calendar format.

Advanced Diagnostics: A visual representation of the data flow from your client to the A.N.U.S. server, the network gateway, and out to the internet nodes.

Customizable Themes: Comes with multiple built-in themes to personalize your dashboard's appearance.

Self-Hosted: Runs on your own hardware with a simple setup script for easy installation.

Sound Alerts: Optional sound effects for critical status changes (connection lost/restored).

🛠️ Tech Stack & Requirements
Category

Technology / Requirement

Backend



Web Server

&

Database



Frontend



OS

Debian 11/12, Ubuntu 22.04+, Raspbian (64-bit)

Deps

ping, nmap, traceroute, dig, curl

The included setup_anus_app.py script will attempt to install dependencies for you.

🚀 Installation
[!IMPORTANT]
🛑 Before You Begin... READ THIS! 🛑
This project is not a beginner's guide to web hosting. It makes several key assumptions about your existing setup and knowledge.

🧠 Assumed Knowledge
You should be comfortable with:

Apache Web Server Configuration: Virtual hosts, .conf files, and basic server administration.

General Web Hosting Concepts: DNS, file permissions, and how a web server serves content.

✅ Required Setup
Before you run the installer, your server must already have:

An existing, fully functional Apache2 installation.

A valid SSL certificate (e.g., from Let's Encrypt) properly configured for your domain.

The script builds on top of your existing secured setup; it does not create it for you.

[!WARNING]
💥 CRITICAL: FOR PRIVATE USE ONLY 💥
This application is intended for private, internal, or personal home-lab use ONLY. It has NOT been security-hardened for a public-facing environment. Exposing it to the open internet could create significant security risks.

[!NOTE]
🧪 Hardware & Testing Notice
This project has only been formally tested on a Raspberry Pi 4 (Raspbian 64-bit) and Ubuntu 25 Server (64-bit). While it may work on other Debian-based systems, your mileage may vary.

<br>

One-Liner Install Methods
Choose one of the methods below. The git method is preferred if you have Git installed.

<details>
<summary><strong>1. Git Clone Method (Recommended)</strong></summary>

This command clones the repository, navigates into the new directory, and executes the automated installer script.

git clone https://github.com/sworrl/ANUS.git && cd ANUS && sudo ./setup_anus_app.py

</details>

<details>
<summary><strong>2. Wget Method</strong></summary>

This command downloads and extracts the repository as a ZIP file, then runs the installer.

wget -qO- https://github.com/sworrl/ANUS/archive/main.zip | sudo apt-get install -y unzip && unzip main.zip -d . && cd ANUS-main && sudo ./setup_anus_app.py

</details>

<br>

Interactive & Manual Install Methods
<details>
<summary><strong>Interactive Menu Install</strong></summary>

For more options, including uninstalling or managing the service, run the setup script with the -menu flag.

Using Git:

git clone https://github.com/sworrl/ANUS.git && cd ANUS && sudo ./setup_anus_app.py -menu

Using Wget:

wget -qO- https://github.com/sworrl/ANUS/archive/main.zip | sudo apt-get install -y unzip && unzip main.zip -d . && cd ANUS-main && sudo ./setup_anus_app.py -menu

</details>

<details>
<summary><strong>Manual Install (Safest Method)</strong></summary>

[!SECURITY]
The automated installation commands download a script and run it with sudo. For maximum security, you should always inspect code before running it with administrator privileges.

Clone the repository:

git clone https://github.com/sworrl/ANUS.git

Navigate into the project directory:

cd ANUS

Inspect the script's contents:

less setup_anus_app.py

If you trust the code, run the installer:

sudo python3 setup_anus_app.py

</details>

⚙️ Configuration
The core of your configuration can be found in two main places:

Targets: To change the hosts that A.N.U.S. monitors, you can edit the JSON file located at /var/www/html/anus/assets/targets.json. The service will automatically pick up changes on its next cycle. The file uses a simple key-value structure where the key is the target's IP or hostname and the value is a boolean (e.g., "8.8.8.8": true).

Dashboard Settings: Other operational settings like the dashboard's update interval, sound effects, theme, and online detection method can all be configured directly from the "Settings" tab within the web interface.

🐛 Troubleshooting
"Failed to fetch network data" on Network Neighborhood:

Ensure nmap is installed: sudo apt-get install nmap.

The setup script attempts to add a sudoers rule. If this failed, you may need to add it manually. Run sudo visudo and add the following line at the end of the file:

www-data ALL=(ALL) NOPASSWD: /usr/bin/nmap, /usr/bin/traceroute

Service fails to start:

Check the service status: systemctl status anus_service.service

View the logs for errors: journalctl -u anus_service.service -f

Dashboard doesn't load or shows errors:

Check Apache status: systemctl status apache2.

Check Apache error logs for PHP issues: tail -f /var/log/apache2/error.log.

Verify permissions on the database file. The user www-data must have write access.

Check the permissions with: ls -l /var/db/anus_metrics.db

⚖️ License
This project is licensed under the GPLv3 License.