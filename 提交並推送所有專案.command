#!/bin/zsh

set -u

project_root="$(cd "$(dirname "$0")" && pwd)" || exit 1
cd "$project_root" || exit 1

pause_and_exit() {
  local status="$1"
  read "?按 Enter 鍵關閉視窗..."
  exit "$status"
}

publish_repository() {
  local label="$1"
  local repository="$2"
  local message="$3"
  local current_branch local_head remote_head merge_base diff_status

  current_branch="$(git -C "$repository" branch --show-current)" || return 1
  if [[ "$current_branch" != "main" ]]; then
    echo "[$label] 只能從 main 分支推送；目前分支：${current_branch:-無}"
    return 1
  fi

  echo "[$label] 檢查遠端主分支..."
  git -C "$repository" fetch origin --prune || return 1
  local_head="$(git -C "$repository" rev-parse HEAD)" || return 1
  remote_head="$(git -C "$repository" rev-parse origin/main)" || return 1
  merge_base="$(git -C "$repository" merge-base HEAD origin/main)" || return 1

  if [[ "$local_head" != "$remote_head" && "$merge_base" == "$local_head" ]]; then
    echo "[$label] origin/main 有較新的變更，請先執行「同步至主版本.command」。"
    return 1
  fi
  if [[ "$local_head" != "$remote_head" && "$merge_base" != "$remote_head" ]]; then
    echo "[$label] 本機與 origin/main 已分歧，請先整理版本後再推送。"
    return 1
  fi

  echo "[$label] 暫存所有變更..."
  git -C "$repository" add -A || return 1
  git -C "$repository" diff --cached --quiet
  diff_status=$?
  if [[ "$diff_status" == "1" ]]; then
    echo "[$label] 建立提交..."
    git -C "$repository" commit -m "$message" || return 1
  elif [[ "$diff_status" != "0" ]]; then
    echo "[$label] 無法檢查暫存變更。"
    return 1
  else
    echo "[$label] 沒有新的變更。"
  fi

  echo "[$label] 推送至遠端..."
  git -C "$repository" push origin HEAD || return 1
  echo "[$label] 完成。"
}

if ! command -v git >/dev/null 2>&1; then
  echo "找不到 Git，請先安裝 Git。"
  pause_and_exit 1
fi

if [[ ! -e "$project_root/.git" ]]; then
  echo "找不到 StockCenter Git 專案。"
  pause_and_exit 1
fi

read "commit_message?提交訊息（直接按 Enter 使用預設值）："
commit_message="${commit_message:-Sync project updates}"

nested_repositories=()
for directory in "$project_root"/*(/N); do
  if [[ -e "$directory/.git" && -f "$directory/update_macro_data.py" ]]; then
    nested_repositories+=("$directory")
  fi
done

for repository in "${nested_repositories[@]}"; do
  if ! publish_repository "Macro ($(basename "$repository"))" "$repository" "$commit_message"; then
    echo "提交與推送未完成。"
    pause_and_exit 1
  fi
done

if ! publish_repository "StockCenter" "$project_root" "$commit_message"; then
  echo "提交與推送未完成。"
  pause_and_exit 1
fi

echo "所有專案均已提交並推送完成。"
pause_and_exit 0
