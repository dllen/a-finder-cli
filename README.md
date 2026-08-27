# A Finder CLI

## 使用 uv

```bash
uv venv
uv sync
```

## 中文使用说明

- 适用场景：查看概览、选股结果、单票信号，以及批量同步沪深 300 区间数据
- 快速开始：完成 uv 环境初始化后，直接运行常用命令即可
- 数据同步：默认会断点续传，可通过参数控制并发、限速与重试策略
- 日志查看：同步过程中会在 logs/ 目录生成成功与失败记录

常用命令说明：

- overview：输出总体概览
- picks：输出选股结果，可用 --top 控制数量
- signals：查询单票信号，可用 --code 指定股票代码
- ma-picks：基于均线条件输出选股结果，可加 --ui 启动界面
- buy-signals：在沪深300中筛选买入信号股票，按推荐优先级排序
- sync-hs300-meta：同步沪深 300 元数据（代码、名称、行业、地区）
- meta：按股票代码查询元数据
- ui：启动 Textual 美化界面，可用 --top / --code / --db 控制展示
- sync-hs300-range：同步沪深 300 区间数据，常用参数：
  - --start / --end：同步起止日期
  - --db：输出数据库文件
  - --concurrency：并发请求数量
  - --rate：每秒请求速率
  - --retries：失败重试次数
  - --backoff：重试退避系数
  - --no-resume：不使用断点续传
  - --only-failed：仅重试失败记录
  - --gap-fill：补齐缺口数据
  - --retry-gaps：重试缺口数据

```bash
uv run a-finder overview
uv run a-finder picks --top 5
uv run a-finder signals --code 600519
uv run a-finder ma-picks --top 5
uv run a-finder ma-picks --top 20 --ui
uv run a-finder buy-signals --top 30
uv run a-finder ui --top 10
uv run a-finder ui --code 600519
uv run a-finder sync-hs300-meta --db hs300.db
uv run a-finder meta --code 600519 --db hs300.db
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --concurrency 6 --rate 8 --retries 4 --backoff 0.6
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --no-resume
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --only-failed
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --gap-fill
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --retry-gaps
```

日志输出：

```bash
logs/fetch_success.log
logs/fetch_failed.log
```

如果提示找不到 a-finder：

```bash
uv sync --reinstall
```

## 直接运行

```bash
python3 stock_cli.py overview
python3 stock_cli.py buy-signals --db hs300.db --top 30
```

## 买入信号筛选

`buy-signals` 在沪深300中筛出买入信号股票，并按推荐优先级排序：
买入信号数 → 策略优先级（均线突破 > 动量突破 > 回调买入 > MACD金叉 > RSI超卖）→ 20日动量。

```bash
python3 stock_cli.py buy-signals --db hs300.db --top 30
```

## 数据源稳定性

- 成分股列表：Eastmoney `RPT_INDEX_COMPONENT`（`columns=ALL`）。
- 日K线：Eastmoney `push2his` 为主，腾讯 `fqkline` 前复权为自动备源。
- 重试：仅对网络类异常重试（指数退避 + 抖动），HTTP 4xx 不重试。

## 均线策略与回测

`ma_backtest.py` 输出均线选股结果并回测一年收益，支持参数寻优与稳健性验证：

```bash
python3 ma_backtest.py --db hs300.db --top 10 --days 240            # 默认策略回测
python3 ma_backtest.py --db hs300.db --top 10 --days 240 --tune     # 搜索回测参数
python3 ma_backtest.py --db hs300.db --top 10 --days 240 --walk-forward  # 滚动训练/验证寻优
python3 ma_backtest.py --db hs300.db --top 10 --days 240 --robust   # 固定留出 + 随机切分稳健性验证
python3 ma_backtest.py --db hs300.db --top 10 --days 240 --search-weights  # 搜索评分权重
python3 ma_backtest.py --db hs300.db --top 10 --days 240 --search-quota   # 搜索形态配额
```

当前固化策略（240 天回测窗口、沪深 300 中 297 只足历史股票）：

| 指标 | 结果 |
|---|---|
| 策略累计收益 | +67.90% |
| 超额收益 | +52.59% |
| 随机切分验证中位超额 | +32.18% |
| 正超额窗口占比 | 100%（10/10） |
| 平均仓位 | 80.59% |

关键参数：形态配额 突破 75% / 回踩 25% / 趋势 0%；趋势对齐深度 2；评分权重 `slope200=3.0`、`momentum20=200`、`momentum10=50`、`volume_bonus=12`。

## 区间同步一键运行

```bash
bash sync_range.sh
bash sync_range.sh 2025-01-01 2026-03-12 hs300.db --concurrency 6 --rate 8 --retries 4 --backoff 0.6
bash sync_range.sh 2025-01-01 2026-03-12 hs300.db --gap-fill
bash sync_range.sh 2025-01-01 2026-03-12 hs300.db --retry-gaps
```

## 元数据 + 行情一键更新

```bash
bash sync_all.sh
bash sync_all.sh 2025-01-01 2026-03-12 hs300.db
bash sync_all.sh 2025-01-01 2026-03-12 hs300.db --concurrency 6 --rate 8 --retries 4 --backoff 0.6
```

## 增量更新 + 选股一键运行

```bash
bash sync_incremental_pick.sh
bash sync_incremental_pick.sh hs300.db 20 ma-picks
bash sync_incremental_pick.sh hs300.db 15 picks --limit 100 --log-level INFO
```

参数说明：

- 第 1 个参数：数据库路径，默认 `hs300.db`
- 第 2 个参数：选股数量 top，默认 `10`
- 第 3 个参数：选股模式，支持 `pick-history` / `picks` / `ma-picks`，默认 `pick-history`（写入 `daily_picks` 表供看板/静态站点使用）
- 第 4 个及之后参数：透传给 `sync-hs300 --mode incremental`，可直接传 `--limit`、`--log-level` 等同步参数

## Web 看板

Flask 后端 + Bootstrap/jQuery（CDN）单页看板，展示每日选股、涨跌表现与历史胜率统计：

```bash
bash run_web.sh                    # 默认 http://127.0.0.1:8000
DB=hs300.db PORT=8080 TOP=20 bash run_web.sh
```

- 日期选择：下拉框列出可用交易日，默认最新交易日（选股页取 `daily_picks`，计划页取 `trade_plan`）
- 「涨跌%」列：最新收盘价相对买入价的涨跌幅（红涨绿跌），移动端卡片同步展示
- 「策略胜率统计」：基于 `pick_outcomes` 历史标注样本，展示每策略样本数/胜率/期望收益，及月度胜率趋势
- 「重算榜单」：不联网，仅重算 `daily_picks`（走 `pick_history.run_picks` 的 `do_sync=False`）
- 「同步行情并重算」：先增量同步沪深300行情再重算榜单（较慢，依赖网络）

## 一键管理

```bash
bash manage.sh status
bash manage.sh start overview
bash manage.sh stop
bash manage.sh restart picks --top 5
```
