#!/usr/bin/env bash
# launchd 调用入口：工作日 15:30 由 com.a-finder.site-data.plist 触发。
# 周末（date +%u: 1=周一 ... 7=周日，>=6 即周末）直接跳过，避免无意义的周末空跑。
# 手动直接跑 build_site_data.sh 不受此限制。
set -euo pipefail
[[ "$(date +%u)" -ge 6 ]] && { echo "$(date +%F) 周末，跳过 site data 构建"; exit 0; }
cd "$(dirname "$0")"
bash build_site_data.sh hs300.db 20 site --push
