#!/usr/bin/env python3

import os
import subprocess
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

def is_screen_installed() -> bool:
    """Checks if the 'screen' package is installed using dpkg."""
    try:
        result = subprocess.run(
            ['dpkg', '-s', 'screen'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False

def is_in_screen() -> bool:
    """
    Checks if running in a screen session using two methods:
    1. Fast check for environment variables (works without sudo).
    2. Slower fallback check of parent processes (works with sudo).
    """
    # Method 1: Check environment variables (fastest)
    if 'STY' in os.environ:
        return True

    # Method 2: Check parent processes (fallback for sudo)
    if PSUTIL_AVAILABLE:
        try:
            parent = psutil.Process(os.getppid())
            # Walk up the process tree until we find 'screen' or hit the top
            while parent.pid > 1:
                if 'screen' in parent.name().lower():
                    return True
                parent = parent.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # A process could have died or we may not have permission
            return False
    
    return False

# --- Main execution block ---
if __name__ == "__main__":
    print("--- Screen Installation Check ---")
    if is_screen_installed():
        print("✅ Status: 'screen' package is installed (found via dpkg).")
    else:
        print("❌ Status: 'screen' package not found or system does not use dpkg.")
    
    print()

    print("--- Current Session Check ---")
    if not PSUTIL_AVAILABLE:
        print("   (Note: 'psutil' is not installed. Sudo check may be inaccurate.)")

    if is_in_screen():
        print("✅ Status: Running inside a GNU screen session.")
        # Under sudo, these might still be missing, but we detected the session
        screen_socket = os.environ.get('STY', 'N/A (hidden by sudo)')
        screen_window = os.environ.get('WINDOW', 'N/A (hidden by sudo)')
        print(f"   Socket Name (STY): {screen_socket}")
        print(f"   Window # (WINDOW): {screen_window}")
    else:
        print("❌ Status: NOT running inside a GNU screen session.")