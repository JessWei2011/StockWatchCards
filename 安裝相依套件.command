#!/bin/zsh
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 Python 3。請先安裝 Python 3.10 以上。"
  exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
echo "安裝完成，請雙擊「啟動Stock2.command」。"
