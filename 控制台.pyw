"""
統一控制台 -- 滑鼠點擊即可開啟三個原本分散的進入點：
- reports_manager.bat          （檔案系統：整理 reports/ 底下的報表分類，上方輸入框可以
                                  直接觸發 stock_report_generator.py 產生/更新報表，右側
                                  排行榜可直接看到卡片系統的 AI 分析內容，不用再另外開
                                  獨立的「分析個股」或「卡片系統」入口）
- 指標數據/update.bat          （總經分析：總經/VIX 追蹤器）
- pattern_viewer/start_viewer.bat （型態教學：K線型態視覺教學介面，串接 data.js 卡片資料）

每個按鈕做的事情，跟你直接在檔案總管雙擊那個檔案完全一樣（用系統預設程式打開），
不重新實作各自的執行邏輯，維持原本各工具獨立運作。
"""
import os
import tkinter as tk
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

WIN_W, WIN_H = 680, 220

TARGETS = [
    ("🗂 檔案系統", os.path.join(BASE_DIR, "reports_manager.bat")),
    ("📈 總經分析", os.path.join(BASE_DIR, "指標數據", "update.bat")),
    ("🎓 型態教學", os.path.join(BASE_DIR, "pattern_viewer", "start_viewer.bat")),
]

# 這兩顆按鈕各自開出來的本機 server（reports_manager.bat -> reports_manager_server.py，
# update.bat -> 指標數據/server.py）都有做 POST /api/shutdown 讓自己乾淨結束。控制台
# 本身沒有在追蹤它們的 process，用打自己的 shutdown API 這個既有機制關掉最簡單，
# 不用去猜 PID／找哪個 process 佔用哪個 port。
SERVER_SHUTDOWN_PORTS = [8934, 8935]


def shutdown_all_servers():
    for port in SERVER_SHUTDOWN_PORTS:
        try:
            req = urllib.request.Request(f"http://localhost:{port}/api/shutdown", method="POST", data=b"")
            urllib.request.urlopen(req, timeout=1)
        except Exception:
            pass  # 那個 server 本來就沒開，或已經關了，不用理會

BG = "#0f1117"
CARD_BG = "#181b22"
BORDER = "#262a36"
TEXT = "#f1f5f9"
TEXT_DIM = "#94a3b8"
ACCENT = "#3b82f6"


def launch(path, status_label):
    if not os.path.exists(path):
        status_label.config(text=f"❌ 找不到檔案：{path}", fg="#ef4444")
        return
    try:
        os.startfile(path)
        status_label.config(text=f"✅ 已開啟：{os.path.basename(path)}", fg="#22c55e")
    except OSError as e:
        status_label.config(text=f"❌ 開啟失敗：{e}", fg="#ef4444")


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

    button_row = tk.Frame(root, bg=BG)
    button_row.pack(pady=6)

    for label, path in TARGETS:
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
            command=lambda p=path: launch(p, status_label),
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
