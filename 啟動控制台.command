#!/bin/zsh -l
set -e

cd "$(dirname "$0")" || exit 1

# 載入 macOS 環境變數與路徑
if [ -x /usr/libexec/path_helper ]; then
  eval "$(/usr/libexec/path_helper -s)"
fi
[ -f ~/.zprofile ] && source ~/.zprofile
[ -f ~/.zshrc ] && source ~/.zshrc

# 尋找支援的 Python (3.10 以上)
PYTHON_BIN=""
for candidate in \
  "$HOME/.local/bin/python3" \
  "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3" \
  "/Library/Frameworks/Python.framework/Versions/Current/bin/python3" \
  "/opt/homebrew/bin/python3" \
  "/usr/local/bin/python3" \
  "$(which python3 2>/dev/null)"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "[錯誤] 找不到 Python 3.10 以上版本。"
  echo "目前系統 python3 版本為：$(python3 --version 2>&1 || echo '未安裝')"
  echo "請安裝 Python 3.10 或更新版本。"
  read "?按 Enter 鍵關閉視窗..."
  exit 1
fi

set +e
"$PYTHON_BIN" launch_stockcenter.py "$@"
RUN_STATUS=$?

if [ $RUN_STATUS -ne 0 ]; then
  echo ""
  echo "[錯誤] 啟動失敗（結束代碼：$RUN_STATUS）。"
  read "?按 Enter 鍵關閉視窗..."
  exit $RUN_STATUS
fi
