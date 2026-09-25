#!/bin/zsh

set -u

cd "$(dirname "$0")" || exit 1

echo "將以遠端 origin/main 覆蓋本機已追蹤的檔案。"
echo "未追蹤的 reports 檔案也會被移除；其他未追蹤檔案會保留。"
read "confirm?是否開始同步？ [y/N] "

if [[ "$confirm" != [yY] ]]; then
  echo "已取消，沒有變更任何檔案。"
  read "?按 Enter 鍵關閉視窗..."
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  echo "找不到 Git，請先安裝 Git。"
  read "?按 Enter 鍵關閉視窗..."
  exit 1
fi

echo "讀取遠端主分支..."
if ! git fetch origin --prune || ! git rev-parse --verify origin/main >/dev/null; then
  echo "無法讀取 origin/main，沒有變更任何檔案。"
  read "?按 Enter 鍵關閉視窗..."
  exit 1
fi

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "切換至 main..."
  if ! git switch --discard-changes main; then
    echo "無法切換至 main，同步未完成。"
    read "?按 Enter 鍵關閉視窗..."
    exit 1
  fi
fi

echo "以 origin/main 更新已追蹤檔案..."
if ! git reset --hard origin/main; then
  echo "同步未完成。"
  read "?按 Enter 鍵關閉視窗..."
  exit 1
fi

git clean -fd -- reports

echo "同步完成：目前已是 origin/main 的版本。"
read "?按 Enter 鍵關閉視窗..."
