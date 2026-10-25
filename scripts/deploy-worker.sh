#!/usr/bin/env bash
# 部署 Cloudflare Worker + D1（持仓云端化）
#
# 用法：bash scripts/deploy-worker.sh
#
# 前置：
#   1. Cloudflare 账户里有 ycu.space 这个 zone
#   2. 已把 d1 create 返回的 database_id 填进 wrangler.toml
#
# 本脚本会：登录 → 建表 → 设密码/密钥 → 部署 → 自检
set -euo pipefail

cd "$(dirname "$0")/.."

# npm 缓存可能不可写，重定向到临时目录
export npm_config_cache="${TMPDIR:-/tmp}/npm-cache-wrangler"
mkdir -p "$npm_config_cache"

WRANGLER="npx --yes wrangler@latest"

echo "=== 0. 前置检查 ==="
if grep -q "REPLACE_WITH_D1_DATABASE_ID" wrangler.toml; then
  echo "❌ wrangler.toml 里的 database_id 还是占位符。"
  echo "   先跑：npx wrangler@latest d1 create a-finder"
  echo "   再把返回的 database_id 填进 wrangler.toml"
  exit 1
fi
echo "✅ database_id 已配置"
grep -E "^database_id" wrangler.toml

echo
echo "=== 1. 登录 Cloudflare（浏览器会弹授权页）==="
$WRANGLER whoami || $WRANGLER login

echo
echo "=== 2. 建表（D1）==="
$WRANGLER d1 execute a-finder --file=worker/schema.sql --remote

echo
echo "=== 3. 设置登录用户名 ==="
echo "（交互式输入，不会进 shell 历史）"
$WRANGLER secret put DECIDE_USER

echo
echo "=== 4. 设置登录密码 ==="
$WRANGLER secret put DECIDE_PASSWORD

echo
echo "=== 5. 设置签名密钥 ==="
echo "建议用：openssl rand -hex 32"
$WRANGLER secret put DECIDE_SECRET

echo
echo "=== 6. 部署 Worker ==="
$WRANGLER deploy

echo
echo "=== 7. 自检 ==="
sleep 3
echo "--- health ---"
curl -s https://ycu.space/api/decide/health || echo "（若失败，等 30s 让路由生效后重试）"
echo
echo "--- 未登录写入应 401 ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST https://ycu.space/api/decide/positions \
  -H 'Content-Type: application/json' \
  -d '{"code":"600519","shares":100,"entry_price":1500}'
echo "--- 错误凭据登录应 401 ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST https://ycu.space/api/decide/login \
  -H 'Content-Type: application/json' \
  -d '{"user":"nobody","password":"wrong"}'

echo
echo "=== 完成 ==="
echo "打开 https://ycu.space/decide.html 验证："
echo "  1) 点「登录」→ 弹出 Modal（非浏览器原生弹窗）"
echo "  2) 输错 → Modal 内红字提示"
echo "  3) 输对 → 顶部显示用户名，可录入持仓"
echo "  4) 刷新页面 → 仍显示用户名（cookie 7 天）"
