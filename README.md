# A.N.U.S. - Automated Network Utility System
![My Animated Logo](./assets/animated-logo.svg)

A.N.U.S. (Automated Network Utility System) is a comprehensive, self-hosted network monitoring dashboard. This tool continuously tracks key internet health metrics like latency, jitter, and packet loss for custom targets, while also monitoring host server resources like CPU, memory, and network throughput. Features a sleek, modern web UI with real-time stats and historical trend charts.

## ✨ Features

  * **Comprehensive Monitoring**: Tracks Ping, Jitter, and Packet Loss for multiple user-defined targets.
  * **Real-time Dashboard**: A sleek, modern UI provides an at-a-glance overview of your entire network's health.
  * **Server Health**: Monitors the host server's CPU usage, memory usage, and network throughput in real-time.
  * **Historical Trends**: An interactive chart displays historical latency data, allowing you to zoom and pan through time to identify patterns.
  * **Event Logging**: Automatically logs network status changes (UP/DOWN) with duration, viewable in both a timeline and a monthly calendar format.
  * **Advanced Diagnostics**: A visual representation of the data flow from your client to the A.N.U.S. server, the network gateway, and out to the internet nodes.
  * **Customizable Themes**: Comes with multiple built-in themes to personalize your dashboard's appearance.
  * **Self-Hosted**: Runs on your own hardware with a simple setup script for easy installation.

## 🖼️ Screenshots

<p align="center">
  <img src="./assets/dashboard-view.png" alt="Main Dashboard View Screenshot" width="700">
  <br>
  <strong>Main Dashboard View</strong>
</p>

<p align="center">
  <img src="./assets/trends-view.png" alt="Historical Trends View Screenshot" width="700">
  <br>
  <strong>Historical Trends View</strong>
</p>

<p align="center">
  <img src="./assets/calendar-view.png" alt="Event Log Calendar View Screenshot" width="700">
  <br>
  <strong>Event Log (Calendar View)</strong>
</p>

## 🛠️ Tech Stack

  * **Backend Service**: Python 3
  * **Web Server / API**: Apache2 & PHP
  * **Database**: SQLite
  * **Frontend**: HTML, Tailwind CSS, Chart.js, Tone.js

## 🚀 Installation

This script is designed for Debian-based Linux distributions (like Ubuntu, Debian, Raspberry Pi OS). You will need `sudo` access to run the installer.

## 🛑 Before You Begin... READ THIS! 🛑

This project is not a beginner's guide to web hosting. It makes several key assumptions about your existing setup and knowledge. Please review the following points carefully before proceeding.

### 🧠 Assumed Knowledge

This project assumes you are already comfortable with:
* 🖥️ **Apache Web Server Configuration:** You should know your way around virtual hosts, `.conf` files, and basic server administration.
* 🌐 **General Web Hosting Concepts:** Familiarity with DNS, file permissions, and how a web server serves content is expected.

We will **not** be covering the basics of setting up a web server from scratch.

### ✅ Required Setup

Before you run the installer, your server **must** already have:
* An existing, fully functional **Apache2** installation.
* 🔒 A valid SSL certificate (e.g., from **Let's Encrypt**) properly configured for your domain.

This script **does not** install or configure Apache or your SSL certificate for you. It builds *on top of* an existing, working, and secured setup.

Please note that this script builds upon your existing server configuration:

✅ It DOES: Create a new anus.conf file to enable the required Apache virtual host.

❌ It DOES NOT: Install the Apache server itself or configure your SSL (Let's Encrypt) certificate.

You must start with a working and secured Apache setup before running this script.

---

### 💥 **CRITICAL WARNING: FOR PRIVATE USE ONLY** 💥

> **This application is intended for private, internal, or personal home-lab use ONLY.**
>
> It has **NOT** been security-hardened for a public-facing environment. Exposing it to the open internet could create significant security risks for your server and network. Please do not point this at the big, scary internet.

---

### 🧪 Hardware & Testing Notice

Heads up! So far, this project has **only** been developed and tested on the following hardware:

* 🍓 **Raspberry Pi 4**

While it may work on other Debian-based systems (like Ubuntu), your mileage may vary. Proceed with caution and be prepared for some tinkering if you are using an untested platform.

### ⚠️ Important Security Note

The installation commands below will download a script from the internet and run it with `sudo` (administrator) privileges. This gives the script full control over your system. For your security, it is **highly recommended** that you inspect the script's code before running it.

The new installation method below downloads the entire project first, making it easy for you to review the `setup_anus_app.py` file before execution.

### One-Liner Install

Both of the methods below will install the software, so **you should only choose one.** The `git clone` method is generally preferred if you have Git installed, as it's the standard way to interact with Git repositories. The `wget` method is a good alternative if you don't have Git on your system or prefer to avoid installing it.

-----

### Git Clone Method

This command uses the **Git** version control system to clone the repository into a new folder named `ANUS`, preserving the entire project history. It then changes into that directory and runs the fully automated installation script.

```bash
git clone [https://github.com/sworrl/ANUS.git](https://github.com/sworrl/ANUS.git) && cd ANUS && sudo ./setup_anus_app.py
```

-----

### Wget Method

This command uses `wget` to download the repository as a ZIP file, then uses `unzip` to extract the files. This method does not preserve the project's commit history. The extracted directory is named `ANUS-main`, and the command then changes into that directory and runs the installation script.

```bash
wget -qO- [https://github.com/sworrl/ANUS/archive/main.zip](https://github.com/sworrl/ANUS/archive/main.zip) | sudo apt-get install -y unzip && unzip main.zip -d . && cd ANUS-main && sudo ./setup_anus_app.py
```

-----

### Interactive Menu Install

Again, choose only one of the following commands to install the software with the interactive menu.

```bash
git clone [https://github.com/sworrl/ANUS.git](https://github.com/sworrl/ANUS.git) && cd ANUS && sudo ./setup_anus_app.py -menu
```bash
wget -qO- https://github.com/sworrl/ANUS/archive/main.zip | sudo apt-get install -y unzip && unzip main.zip -d . && cd ANUS-main && sudo ./setup_anus_app.py -menu
```

### Interactive Menu Install

For more options, including uninstalling or managing the service, use the following command to run the setup script with the interactive menu.

```bash
git clone https://github.com/sworrl/ANUS.git && cd ANUS && sudo ./setup_anus_app.py -menu
```bash
wget -qO- [https://github.com/sworrl/ANUS/archive/main.zip](https://github.com/sworrl/ANUS/archive/main.zip) | sudo apt-get install -y unzip && unzip main.zip -d . && cd ANUS-main && sudo ./setup_anus_app.py -menu
```

### Manual Install (Safest Method)

For maximum security, you should manually review the code before running it.

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/sworrl/ANUS.git](https://github.com/sworrl/ANUS.git)
    ```
2.  **Navigate into the project directory:**
    ```bash
    cd ANUS
    ```
3.  **Inspect the script's contents** using a text editor or a command like `less`:
    ```bash
    less ANUS/setup_anus_app.py
    ```
4.  **If you trust the code**, run the installer:
    ```bash
    sudo python3 ANUS/setup_anus_app.py
    ```
### Interactive Menu Install

For more options, including uninstalling or managing the service, run the setup script with the `-menu` flag.

```bash
wget [https://raw.githubusercontent.com/sworrl/ANUS/main/ANUS/setup_anus_app.py](https://raw.githubusercontent.com/sworrl/ANUS/main/ANUS/setup_anus_app.py) && sudo python3 setup_anus_app.py -menu
```

## ⚙️ Configuration

  * **Targets**: To change the hosts that A.N.U.S. monitors, you can edit the `targets.json` file located in the web directory (`/var/www/html/anus/targets.json` by default). The Python service will automatically pick up the changes on its next cycle.
  * **Dashboard Settings**: The update interval, sound effects, theme, and online detection method can all be configured directly from the "Settings" tab in the web interface.

## ⚖️ License

This project is licensed under the GPLv3 License.
