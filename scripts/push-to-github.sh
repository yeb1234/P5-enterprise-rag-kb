#!/usr/bin/env bash
# 把本地分支推到你的 GitHub / Gitee 仓库，并用 gh 批量建 PR。
#
# 用法：
#   bash scripts/push-to-github.sh git@github.com:you/kb-demo.git
#   bash scripts/push-to-github.sh https://github.com/you/kb-demo.git
#
# 前提：
#   1) 你已在 GitHub/Gitee 建好空仓库（不要勾选自动生成 README，避免首推冲突）；
#   2) GitHub 想自动建 PR 需安装 gh 并执行过 `gh auth login`；Gitee 只能推分支后网页建 PR。
#
# 说明：本脚本不读取、不保存任何令牌；凭证由 git / gh 自己的凭据管理负责。
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE_URL="${1:-}"
if [ -z "$REMOTE_URL" ]; then
  echo "用法: bash scripts/push-to-github.sh <仓库地址>" >&2
  exit 1
fi

# 用独立的 remote 名，保留本地 .data/remote.git 作为离线 origin
if git remote get-url github >/dev/null 2>&1; then
  git remote set-url github "$REMOTE_URL"
else
  git remote add github "$REMOTE_URL"
fi

echo "==> 推送主线 demo/v1"
git push github demo/v1

echo "==> 推送 feature 分支"
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/feature/); do
  echo "    $b"
  git push github "$b"
done

if command -v gh >/dev/null 2>&1; then
  echo "==> 用 gh 创建 PR（已存在则跳过）"
  for n in 1 2 3 4 5 6; do
    f="docs/pr/PR-$n.md"
    [ -f "$f" ] || continue
    branch=$(sed -n 's/^| 分支 | `\([^`]*\)`.*/\1/p' "$f" | head -n1)
    title=$(head -n1 "$f" | sed 's/^# //')
    [ -n "$branch" ] || continue
    echo "    PR #$n <- $branch"
    gh pr create --base demo/v1 --head "$branch" --title "$title" --body-file "$f" || \
      echo "      （已存在或无权限，跳过）"
  done
else
  echo "==> 未安装 gh：分支已推送。请按 docs/pr/PR-*.md 的描述在网页上建 PR。"
fi

echo
echo "完成。远端分支："
git branch -r | grep github || true
