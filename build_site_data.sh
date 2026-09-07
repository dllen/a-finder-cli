#!/usr/bin/env bash
# 本地一键生成网站数据：同步 + 选股 + 计划 + 全档位组合 + 导出静态 JSON
# 用法: bash build_site_data.sh [数据库路径] [选股数量] [导出目录] [--push]
# 示例: bash build_site_data.sh hs300.db 20 site --push
#
# --push: 构建成功后，把导出目录 force-add 到一个 orphan commit 并 force-push
#         到 gh-pages 分支（与 CI 的 peaceiris 同模型）。site/ 是 gitignore 的，
#         永远不进 main；此处用临时 git index 推送，不动工作树与当前分支。
#
# 本脚本与 .github/workflows/fetch-data.yml 的步骤保持一致（同步 → plan build →
# build-all → build-all --backfill），使本地与 CI 产出同构数据集。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-20}"
EXPORT_DIR="${3:-site}"
PUSH=0
[[ "${4:-}" == "--push" ]] && PUSH=1

# 回补起点：系统 go-live 锚点。build-all --backfill 幂等（跳过已有日期），
# 所以固定锚点 = “从 go-live 到今天补齐缺口”。如需调整，同步改 fetch-data.yml。
BACKFILL_ANCHOR="2026-08-01"

echo "=== 本地数据生成脚本 ==="
echo "数据库: $DB"
echo "选股数量: $TOP"
echo "导出目录: $EXPORT_DIR"
echo "推送 gh-pages: $([[ $PUSH == 1 ]] && echo yes || echo no)"
echo ""

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
}

push_site_to_ghpages() {
  local export_dir="$1"
  [[ -f "$export_dir/index.html" ]] || { echo "❌ $export_dir/index.html 缺失，跳过推送" >&2; exit 1; }
  cd "$ROOT_DIR"
  # 临时 git index：force-add 被忽略的 site/，构造 orphan tree 并 commit-tree，
  # 然后 force-push 到远端 gh-pages。全程不动工作树与当前分支。
  local tmp_index tree commit
  tmp_index="$(mktemp)"
  GIT_INDEX_FILE="$tmp_index" git add -f "$export_dir"
  tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"
  rm -f "$tmp_index"
  commit="$(git commit-tree "$tree" -m "update site data $(date +%F-%H:%M)")"
  git push --force origin "$commit:gh-pages"
  echo "✅ gh-pages 已更新 -> $commit"
}

# 1. 增量同步 + 选股
echo "[1/5] 增量同步 + 选股..."
bash "$ROOT_DIR/sync_incremental_pick.sh" "$DB" "$TOP" pick-history

# 2. 生成今日交易计划
PLAN_DATE="$(date +%Y-%m-%d)"
echo "[2/5] 生成 $PLAN_DATE 交易计划..."
run_cmd plan build --db "$DB" --date "$PLAN_DATE"

# 3. 构建所有资金档位组合（5W-50W，10 档）
echo "[3/5] 构建 10 个资金档位组合..."
run_cmd plan build-all --db "$DB" \
  --strategy linyuan --tiers 50000,100000,150000,200000,250000,300000,350000,400000,450000,500000

# 4. 回补缺失日期的组合数据（从 BACKFILL_ANCHOR 到今天，幂等）
echo "[4/5] 回补历史组合数据（since $BACKFILL_ANCHOR）..."
run_cmd plan build-all --backfill --since "$BACKFILL_ANCHOR" --db "$DB"

# 5. 导出静态 JSON 数据
echo "[5/5] 导出静态数据到 $EXPORT_DIR..."
python3 "$ROOT_DIR/export_json.py" --db "$DB" --out "$EXPORT_DIR"

# 6. 复制必要的静态文件
echo "复制静态文件..."
cp "$EXPORT_DIR/index.html" "$EXPORT_DIR/404.html"
touch "$EXPORT_DIR/.nojekyll"

echo ""
echo "✅ 数据生成完成！"
echo "导出目录: $EXPORT_DIR"
echo "HTML 文件: $(ls -1 "$EXPORT_DIR"/*.html 2>/dev/null | wc -l | tr -d ' ') 个"
echo "JSON 数据: $(ls -1 "$EXPORT_DIR/data/"*.json 2>/dev/null | wc -l | tr -d ' ') 个"

if [[ "$PUSH" == "1" ]]; then
  echo ""
  push_site_to_ghpages "$EXPORT_DIR"
  echo ""
  echo "下一步：等 GitHub Pages 静态站点刷新（通常 1-2 分钟）"
else
  echo ""
  echo "下一步："
  echo "  - 本地预览: cd $EXPORT_DIR && python3 -m http.server 8000"
  echo "  - 推送部署: bash build_site_data.sh $DB $TOP $EXPORT_DIR --push"
  echo "  - 定时部署: launchctl load com.a-finder.site-data.plist（见 run_local_cron.sh）"
fi
