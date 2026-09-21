#!/usr/bin/env python3
"""Windows 與 macOS 共用的 StockCenter 啟動入口。"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
MINIMUM_PYTHON = (3, 10)
REQUIRED_MODULES = {
    "Pillow": "PIL",
    "pystray": "pystray",
    "requests": "requests",
}


def main() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        version = ".".join(map(str, MINIMUM_PYTHON))
        print(f"需要 Python {version} 以上；目前版本為 {sys.version.split()[0]}。")
        print("請安裝新版 Python 後重新執行啟動檔。")
        return 1

    missing = [
        package for package, module in REQUIRED_MODULES.items()
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        requirements_file = ROOT_DIR / "requirements.txt"
        print("缺少必要套件：" + ", ".join(missing))
        print(f'請先執行："{sys.executable}" -m pip install -r "{requirements_file}"')
        return 1

    controller = ROOT_DIR / "控制台.pyw"
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists() and sys.executable.lower().endswith("python.exe"):
            import subprocess
            subprocess.Popen(
                [str(pythonw), str(controller)],
                cwd=str(ROOT_DIR),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
            )
            return 0

    namespace = {"__name__": "__main__", "__file__": str(controller)}
    exec(compile(controller.read_bytes(), str(controller), "exec"), namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
