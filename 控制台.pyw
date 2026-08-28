"""統一控制台：低負載的系統匣服務管理器。"""
import ctypes
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from datetime import datetime
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
UPDATE_SCRIPT = MACRO_DIR / "update_macro_data.py"
UPDATE_LOG = MACRO_DIR / "macro_update.log"
UPDATE_STATUS_FILE = MACRO_DIR / "macro_update_status.json"

stop_event = threading.Event()
update_lock = threading.Lock()
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


def append_update_log(message: str) -> None:
    try:
        if UPDATE_LOG.exists() and UPDATE_LOG.stat().st_size > 1_000_000:
            UPDATE_LOG.replace(UPDATE_LOG.with_suffix(".log.1"))
        with UPDATE_LOG.open("a", encoding="utf-8") as log_file:
            log_file.write(f"\n{'=' * 72}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n{message}\n")
    except OSError:
        pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_update_status(state: str, run_id: str, **details) -> None:
    """控制台處理啟動/逾時等更新程式本身來不及回報的狀態。"""
    payload = {
        "state": state,
        "runId": run_id,
        "updatedAt": now_iso(),
        **details,
    }
    try:
        temp_file = UPDATE_STATUS_FILE.with_suffix(UPDATE_STATUS_FILE.suffix + ".tmp")
        temp_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(UPDATE_STATUS_FILE)
    except OSError as error:
        append_update_log(f"無法寫入更新狀態：{error}")


def read_update_status() -> dict:
    try:
        data = json.loads(UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run_macro_update(open_page: bool = False) -> str:
    """更新永不重疊，避免同時抓取資料與 Git 寫入造成資源和鎖定問題。"""
    if not update_lock.acquire(blocking=False):
        if open_page:
            open_workspace("macro")
        return "updating"
    run_id = uuid.uuid4().hex
    started_at = now_iso()
    try:
        write_update_status(
            "updating", run_id, startedAt=started_at, phase="starting",
            message="正在啟動總經資料更新…", updatedFields=[], failedFields=[],
        )
        if open_page:
            open_workspace("macro")
        result = subprocess.run(
            [str(PYTHONW), str(UPDATE_SCRIPT)],
            cwd=MACRO_DIR,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=20 * 60,
        )
        output = (result.stdout + result.stderr).strip() or "沒有輸出"
        append_update_log(f"結束碼：{result.returncode}\n{output}")
        status = read_update_status()
        if status.get("runId") == run_id and status.get("state") in {"success", "partial", "failed"}:
            return status["state"]

        error = f"更新程式異常結束（結束碼 {result.returncode}），沒有產生完整結果。"
        write_update_status(
            "failed", run_id, startedAt=started_at, finishedAt=now_iso(), phase="failed",
            message="更新失敗，未載入舊資料。", error=error,
            updatedFields=[], failedFields=[],
        )
        return "failed"
    except subprocess.TimeoutExpired:
        append_update_log("更新逾時（20 分鐘），已停止等待。")
        write_update_status(
            "failed", run_id, startedAt=started_at, finishedAt=now_iso(), phase="failed",
            message="更新逾時，未載入舊資料。", error="更新超過 20 分鐘。",
            updatedFields=[], failedFields=[],
        )
        return "failed"
    except OSError as error:
        append_update_log(f"無法執行更新：{error}")
        write_update_status(
            "failed", run_id, startedAt=started_at, finishedAt=now_iso(), phase="failed",
            message="無法啟動更新程式，未載入舊資料。", error=str(error),
            updatedFields=[], failedFields=[],
        )
        return "failed"
    finally:
        update_lock.release()


def open_workspace(server_key: str) -> None:
    server = SERVERS[server_key]
    start_server(server)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not is_port_open(server["port"]):
        time.sleep(0.05)
    webbrowser.open(server["url"])


def update_now(_icon=None, _item=None) -> None:
    threading.Thread(target=run_macro_update, name="macro-update-manual", daemon=True).start()


def start_all(_icon=None, _item=None) -> None:
    threading.Thread(target=ensure_servers, name="server-start", daemon=True).start()


def stop_all(_icon=None, _item=None) -> None:
    threading.Thread(target=stop_servers, name="server-stop", daemon=True).start()


def show_stock(_icon=None, _item=None) -> None:
    open_workspace("stock")


def refresh_macro_and_open() -> None:
    """先開狀態頁顯示進度；資料只有在更新成功或部分成功後才會載入。"""
    run_macro_update(open_page=True)


def show_macro(_icon=None, _item=None) -> None:
    threading.Thread(
        target=refresh_macro_and_open,
        name="macro-update-before-open",
        daemon=True,
    ).start()


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
            pystray.MenuItem("開啟總經分析", show_macro),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即更新總經數據（手動）", update_now),
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
