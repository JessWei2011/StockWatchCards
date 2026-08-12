"""
統一控制台 -- 滑鼠點擊即可開啟三個原本分散的進入點：
- stock_report_generator.py    （分析個股：產生/更新個股分析報表）
- reports_manager.bat          （檔案系統：整理 reports/ 底下的報表分類，右側排行榜可直接
                                  看到卡片系統的 AI 分析內容，不用再另外開卡片系統）
- 指標數據/update.bat          （總經分析：總經/VIX 追蹤器）

每個按鈕做的事情，跟你直接在檔案總管雙擊那個檔案完全一樣（用系統預設程式打開），
不重新實作各自的執行邏輯，維持原本各工具獨立運作。
"""
import os
import tkinter as tk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TARGETS = [
    ("📊 分析個股", os.path.join(BASE_DIR, "stock_report_generator.py")),
    ("🗂 檔案系統", os.path.join(BASE_DIR, "reports_manager.bat")),
    ("📈 總經分析", os.path.join(BASE_DIR, "指標數據", "update.bat")),
]

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
    root.geometry("420x360")
    root.resizable(False, False)

    tk.Label(
        root, text="📌 統一控制台", font=("Microsoft JhengHei", 18, "bold"), bg=BG, fg=TEXT
    ).pack(pady=(24, 18))

    status_label = tk.Label(root, text="", font=("Microsoft JhengHei", 9), bg=BG, fg=TEXT_DIM, wraplength=380)

    for label, path in TARGETS:
        btn = tk.Button(
            root,
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
            width=28,
            cursor="hand2",
            command=lambda p=path: launch(p, status_label),
        )
        btn.pack(pady=6)

    status_label.pack(pady=(14, 24))

    root.mainloop()


if __name__ == "__main__":
    main()
