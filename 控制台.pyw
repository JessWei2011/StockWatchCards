"""
統一控制台 -- 滑鼠點擊即可開啟兩個主要工作區：
- reports_manager.bat          （個股分析中心：整合 reports/ 檔案管理、報表產生/更新、
                                  AI 分析排行榜與型態視覺教學，共用同一個本機 server）
- 指標數據/update.bat          （總經分析：總經/VIX 追蹤器）

每個按鈕做的事情，跟你直接在檔案總管雙擊那個檔案完全一樣（用系統預設程式打開），
不重新實作各自的執行邏輯，維持原本各工具獨立運作。
"""
import os
import queue
import subprocess
import threading
import tkinter as tk
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

WIN_W, WIN_H = 500, 220

TARGETS = [
    ("📊 個股分析中心", os.path.join(BASE_DIR, "reports_manager.bat"), True),
    ("📈 總經分析", os.path.join(BASE_DIR, "指標數據", "update.bat"), False),
]

# 兩個工作區各自的本機 server（reports_manager.bat -> reports_manager_server.py，
# update.bat -> 指標數據/server.py）都有做 POST /api/shutdown 讓自己乾淨結束。控制台
# 本身沒有在追蹤它們的 process，用打自己的 shutdown API 這個既有機制關掉最簡單，
# 不用去猜 PID／找哪個 process 佔用哪個 port。
SERVER_SHUTDOWN_PORTS = [8934, 8935]


def shutdown_all_servers():
    def _worker():
        for port in SERVER_SHUTDOWN_PORTS:
            try:
                req = urllib.request.Request(f"http://localhost:{port}/api/shutdown", method="POST", data=b"")
                urllib.request.urlopen(req, timeout=0.5)
            except Exception:
                pass  # 那個 server 本來就沒開，或已經關了，不用理會
    t = threading.Thread(target=_worker, daemon=True)
    t.start()

BG = "#0f1117"
CARD_BG = "#181b22"
BORDER = "#262a36"
TEXT = "#f1f5f9"
TEXT_DIM = "#94a3b8"
ACCENT = "#3b82f6"
UI_QUEUE = queue.Queue()


def launch(path, status_label, update_git=False):
    if not os.path.exists(path):
        status_label.config(text=f"❌ 找不到檔案：{path}", fg="#ef4444")
        return

    def worker():
        git_warning = ""
        if update_git:
            try:
                result = subprocess.run(
                    ["git", "pull", "--ff-only"], cwd=BASE_DIR,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=120,
                )
                if result.returncode != 0:
                    output = (result.stderr or result.stdout or "未知錯誤").strip().splitlines()
                    git_warning = output[-1] if output else "未知錯誤"
            except (OSError, subprocess.TimeoutExpired) as e:
                git_warning = str(e)

        try:
            os.startfile(path)
            if git_warning:
                message = f"⚠ Git 更新失敗，但已開啟現有版本：{git_warning}"
                color = "#f59e0b"
            else:
                message = f"✅ {'Git 已更新並' if update_git else ''}開啟：{os.path.basename(path)}"
                color = "#22c55e"
        except OSError as e:
            message = f"❌ 開啟失敗：{e}"
            color = "#ef4444"
        UI_QUEUE.put((status_label, message, color))

    if update_git:
        status_label.config(text="⏳ 正在從 Git 更新程式碼…", fg=TEXT_DIM)
    threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    root.title("統一控制台")
    root.configure(bg=BG)
    root.resizable(False, False)

    # 置中顯示，不再貼工作列(多螢幕/DPI 縮放下貼工作列的座標算不準，容易跑到螢幕外)。
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - WIN_W) // 2
    y = (screen_h - WIN_H) // 2
    root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    tk.Label(
        root, text="📌 統一控制台", font=("Microsoft JhengHei", 18, "bold"), bg=BG, fg=TEXT
    ).pack(pady=(24, 18))

    status_label = tk.Label(root, text="", font=("Microsoft JhengHei", 9), bg=BG, fg=TEXT_DIM, wraplength=440)

    def process_ui_queue():
        try:
            while True:
                label, message, color = UI_QUEUE.get_nowait()
                label.config(text=message, fg=color)
        except queue.Empty:
            pass
        root.after(100, process_ui_queue)

    root.after(100, process_ui_queue)

    button_row = tk.Frame(root, bg=BG)
    button_row.pack(pady=6)

    for label, path, update_git in TARGETS:
        btn = tk.Button(
            button_row,
            text=label,
            font=("Microsoft JhengHei", 12, "bold"),
            bg=CARD_BG,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=14,
            pady=12,
            width=16,
            cursor="hand2",
            command=lambda p=path, should_update=update_git: launch(p, status_label, should_update),
        )
        btn.pack(side=tk.LEFT, padx=8)

    status_label.pack(pady=(14, 24))

    def on_close():
        shutdown_all_servers()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
