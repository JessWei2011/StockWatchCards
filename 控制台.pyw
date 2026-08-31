"""統一控制台：低負載的系統匣服務管理器。"""
import ctypes
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw


BASE_DIR = Path(__file__).resolve().parent
MACRO_DIR = BASE_DIR / "指標數據"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
if not PYTHONW.exists():
    PYTHONW = Path(sys.executable)

SERVERS = {
    "stock": {
        "port": 8935,
        "cwd": BASE_DIR,
        "script": BASE_DIR / "reports_manager_server.py",
        "url": "http://localhost:8935/reports_manager.html",
    },
    "macro": {
        "port": 8934,
        "cwd": MACRO_DIR,
        "script": MACRO_DIR / "server.py",
        "url": "http://localhost:8934/macro-tracker-offline.html",
    },
}

stop_event = threading.Event()
instance_mutex = None


def is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def start_server(server: dict) -> bool:
    """只在連接埠尚未被使用時啟動，避免重複常駐程序。"""
    if is_port_open(server["port"]):
        return False
    subprocess.Popen(
        [str(PYTHONW), str(server["script"])],
        cwd=server["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return True


def ensure_servers() -> None:
    for server in SERVERS.values():
        start_server(server)


def stop_servers() -> None:
    """利用既有 shutdown API 停止服務，不依 PID 強制結束其他程式。"""
    for server in SERVERS.values():
        try:
            request = urllib.request.Request(
                f"http://localhost:{server['port']}/api/shutdown", method="POST", data=b""
            )
            urllib.request.urlopen(request, timeout=1).close()
        except OSError:
            pass


def wait_for_servers_to_stop(timeout: float = 3.0) -> None:
    """等待 shutdown API 真正釋放連接埠，再允許控制台自己結束。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(is_port_open(server["port"]) for server in SERVERS.values()):
            return
        time.sleep(0.1)


def open_workspace(server_key: str) -> None:
    server = SERVERS[server_key]
    start_server(server)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not is_port_open(server["port"]):
        time.sleep(0.05)
    webbrowser.open(server["url"])


def start_all(_icon=None, _item=None) -> None:
    threading.Thread(target=ensure_servers, name="server-start", daemon=True).start()


def stop_all(_icon=None, _item=None) -> None:
    threading.Thread(target=stop_servers, name="server-stop", daemon=True).start()


def show_stock(_icon=None, _item=None) -> None:
    open_workspace("stock")


def quit_controller(icon, _item=None) -> None:
    stop_event.set()
    stop_servers()
    wait_for_servers_to_stop()
    icon.stop()


def make_icon_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), "#0f1117")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 59, 59), radius=12, fill="#1d4ed8")
    draw.line((16, 43, 28, 31, 37, 36, 50, 18), fill="white", width=5, joint="curve")
    draw.ellipse((45, 14, 55, 24), fill="#86efac")
    return image


def main() -> None:
    # Windows 命名 mutex：從啟動資料夾、桌面捷徑或手動雙擊開啟，都只保留一個控制器。
    global instance_mutex
    instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Stock2UnifiedController")
    if not instance_mutex or ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    try:
        ensure_servers()
        menu = pystray.Menu(
            pystray.MenuItem("開啟個股分析中心", show_stock, default=True),
            pystray.MenuItem("啟動所有服務", start_all),
            pystray.MenuItem("停止所有服務", stop_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("結束統一控制台", quit_controller),
        )
        icon = pystray.Icon("stock2-control", make_icon_image(), "統一控制台", menu)
        icon.run()
    finally:
        # 右鍵退出以外的結束路徑也不能留下服務或 mutex。
        stop_servers()
        wait_for_servers_to_stop()
        if instance_mutex:
            ctypes.windll.kernel32.CloseHandle(instance_mutex)


if __name__ == "__main__":
    main()
