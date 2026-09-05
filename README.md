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
- plan：交易计划（paper-trade 纸面交易），子命令 `build` [--capital] [--backfill]
- linyuan-picks：林园策略选股，财务质量主导（连续5年毛利率>40% ∧ 扣非ROE>15%），过滤医药/中药/食品饮料/高端制造
- sync-fundamentals-history：同步历年财务到 fundamentals_history
- sync-industry：从 akshare 拉行业回填 hs300_metadata.industry
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
uv run a-finder plan build --capital 100000            # 默认 10W，校验 5 档（5/10/20/30/50W）
uv run a-finder plan build --capital 50000 --backfill   # 用 5W 重建历史交易计划
uv run a-finder sync-hs300-meta --db hs300.db
uv run a-finder meta --code 600519 --db hs300.db
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --concurrency 6 --rate 8 --retries 4 --backoff 0.6
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --no-resume
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --only-failed
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --gap-fill
uv run a-finder sync-hs300-range --start 2025-01-01 --end 2026-03-12 --db hs300.db --retry-gaps
uv run a-finder linyuan-picks --top 20
uv run a-finder linyuan-picks --top 20 --dry-run
uv run a-finder sync-industry --db hs300.db
uv run a-finder sync-fundamentals-history --db hs300.db --concurrency 6 --rate 5
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

## 林园策略 / LinYuan

财务质量主导的选股策略：行业白名单 + 连续 5 年毛利率/扣非 ROE 双门槛。

| 维度 | 阈值 |
|---|---|
| 行业 | 医药生物 / 中药 / 食品饮料 / 机械设备 / 电力设备 / 汽车整车 |
| 毛利率（连续 5 年） | > 40% |
| 扣非 ROE（连续 5 年） | > 15% |

**数据底座**：先用 `sync-industry` 回填 `hs300_metadata.industry`，再 `sync-fundamentals-history` 拉历年财务指标到 `fundamentals_history`。两表缺一不可。

```bash
uv run a-finder sync-industry --db hs300.db                     # 一次性回填行业
uv run a-finder sync-fundamentals-history --db hs300.db         # 拉历年财务
uv run a-finder linyuan-picks --top 20                          # 跑林园策略
```

输出落在终端表格；`--dry-run` 仅打印候选不写库。

## 交易计划 / Trade Plan

`plan build` 从 `daily_picks` 出发，按技术指标 + 风险预算生成当日 **paper-trade 纸面交易计划**，写入 `trade_plan` 表；持仓跟踪通过 `open_positions` / `trade_events` 自动串联，形成端到端的"选股 → 计划 → 持仓 → PnL"闭环。

### 资金档位（A 股 100 股一手）

| 档位 | 金额 | 档位 | 金额 |
|---|---|---|---|
| 5W  | 50,000 元  | 30W | 300,000 元 |
| 10W | 100,000 元（**默认**） | 35W | 350,000 元 |
| 15W | 150,000 元 | 40W | 400,000 元 |
| 20W | 200,000 元 | 45W | 450,000 元 |
| 25W | 250,000 元 | 50W | 500,000 元 |

5W → 50W 每 5W 一档共 **10 档**；CLI `--capital` 与 plan 页 btn-group 切换器同源。

按资金算股数采用向下取整的纯函数：

```
shares = floor(capital × size_pct / (price × 100)) × 100
```

不足一手的标的返回 0 股，UI 标记为 **"资金不足一手"**（跳过建仓，paper-trade 也不会模拟成交）。每只票独立算股数，**不做总仓缩放**——`params_hash` 包含 capital，换档重建会覆盖原 shares（`INSERT ... ON CONFLICT DO UPDATE`）。

非法档位（如 `--capital 999999`）回退到默认 10W；`--backfill` 会对历史日期重新生成 plan。

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
- 交易计划页 `/plan`：顶部标题带 `paper` 徽标；筛选支持「含 failed」「只显示可建仓」两个复选框（后者按当前资金档位筛掉「0 股 资金不足一手」行）+ 「档位筛选（交集）」多选下拉（勾哪几档就只保留那些档位都能建仓的票，label 显示如「5W+10W」或「不限」）；表格列包含 **代码 / 名称 / 方向 / 策略 / 计划价 / 仓位 / 股数 / 止损 / 止盈 / RR / 状态 / 理由**；理由列折叠面板里附「各档股数 5W-50W」子表，每只 buy 票一次性展示 10 档位的可建仓股数（hold/exit 不渲染），方便跨档位选股；摘要卡显示资金档位 + 已用/现金/利用率（>100% 高亮红）+ 买入合计仓位与股数；点 5W/10W/15W/20W/25W/30W/35W/40W/45W/50W 按钮即时按当前资金重算每行股数与汇总；下方「持仓跟踪」展示 open_positions 的浮动盈亏与止损/止盈预期

## 一键管理

```bash
bash manage.sh status
bash manage.sh start overview
bash manage.sh stop
bash manage.sh restart picks --top 5
```
