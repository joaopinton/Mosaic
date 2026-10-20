import socket
import subprocess
import time
import webbrowser
import os
import sys

WORKSPACE_DIR = r"e:\padova moto days 2026"
URL = "http://127.0.0.1:8000/"

def is_server_running(host='127.0.0.1', port=8000):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        res = s.connect_ex((host, port))
        return res == 0
    finally:
        s.close()

def main():
    if not is_server_running():
        # Start server silently in the background
        flags = 0
        if sys.platform == 'win32':
            flags = subprocess.CREATE_NO_WINDOW
            
        subprocess.Popen(
            [sys.executable.replace('python.exe', 'pythonw.exe'), "-m", "http.server", "8000", "--directory", WORKSPACE_DIR],
            creationflags=flags,
            cwd=WORKSPACE_DIR
        )
        # Wait up to 3 seconds for server
        for _ in range(10):
            time.sleep(0.3)
            if is_server_running():
                break

    # Open default browser
    webbrowser.open(URL)

if __name__ == "__main__":
    main()
