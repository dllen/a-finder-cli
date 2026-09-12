# TODO

## 待办：K 线限流解除后重跑 site --push

- **创建**：2026-09-08
- **状态**：✅ 完成（2026-09-12）

### 背景

2026-09-08 计算当日数据时，K 线数据源对本机限流：

| 接口 | 症状 |
|---|---|
| 东财 `push2his`（K线主源） | `RemoteDisconnected`（连接被重置） |
| 腾讯 `fqkline`（K线备源） | `HTTP 501 Not Implemented` |
| 东财 `datacenter-web`（成分股） | 正常 |

`daily_prices` 已覆盖到 **2026-09-07**，缺 09-08 当日。为避免用旧价算出标成 09-08 的脏数据，已停流水线、未推送。

### 完成条件

重跑成功且 `gh-pages` 已更新到 2026-09-08 数据（`logs/fetch_success.log` 出现 `2026-09-08` 成功记录）。

### 命令

```bash
# 1) 探活：任一 K 线源返回数据即恢复（脚本见 docs/usage.md「故障处理」）

# 2) 重跑全流程并推送
bash a-finder.sh site hs300.db 20 site --push
```

### 判别

- `logs/fetch_failed.log` 大量 `501` / `RemoteDisconnected` 且 `logs/fetch_success.log` 不再新增 → 仍限流，继续等。
- 限流期间勿反复重跑（会加重限流）；降速试探可用 `--rate 2 --concurrency 2`。

### 完成记录（2026-09-12）

- 限流解除后，东财/腾讯并发同步仍会二次触发限流；改用 **akshare（升级到 1.18.94）`stock_zh_a_daily`(新浪)/`stock_zh_a_hist_tx`(腾讯) + 并发1 + 人工随机延时** 补齐缺口（volume 股→手 ÷100）。
- `data_providers.py` 新增新浪第三备源（东财→腾讯→新浪）。
- 数据补齐到 09-11 后，跳过 `site` 内置 sync（gap-fill 会锤节假日段），直接 pick→plan→build-all→backfill→export→push。
- gh-pages 已更新到 09-11 数据（commit `07b08ff`）。
