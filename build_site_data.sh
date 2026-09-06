#!/usr/bin/env bash
# 本地一键生成网站数据：同步 + 选股 + 计划 + 全档位组合 + 导出静态 JSON
# 用法: bash build_site_data.sh [数据库路径] [选股数量] [导出目录]
# 示例: bash build_site_data.sh hs300.db 20 site
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${1:-hs300.db}"
TOP="${2:-20}"
EXPORT_DIR="${3:-site}"

echo "=== 本地数据生成脚本 ==="
echo "数据库: $DB"
echo "选股数量: $TOP"
echo "导出目录: $EXPORT_DIR"
echo ""

run_cmd() {
  if command -v uv >/dev/null 2>&1; then
    uv run a-finder "$@"
  else
    python3 "$ROOT_DIR/stock_cli.py" "$@"
  fi
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
  --strategy linyuan \
  --tiers 50000,100000,150000,200000,250000,300000,350000,400000,450000,500000

# 4. 回补缺失日期的组合数据（从 2026-08-01 到今天）
echo "[4/5] 回补历史组合数据..."
run_cmd plan build-all --backfill --since 2026-08-01 --db "$DB"

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
echo ""
echo "下一步："
echo "  - 本地预览: cd $EXPORT_DIR && python3 -m http.server 8000"
echo "  - 提交推送: git add . && git commit -m 'update site data' && git push"
echo "  - 部署到 GitHub Pages: 推送后会自动触发 workflow"
