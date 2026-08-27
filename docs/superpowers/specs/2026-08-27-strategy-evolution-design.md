# 选股策略自我进化闭环 — 设计文档

日期：2026-08-27
状态：已确认（方案 A：胜率排名 + 比例配额 + 挑战者门禁）

## 1. 目标与背景

根据历史模拟交易盈亏数据，自动进化选股策略，提升榜单胜率。

当前事实基线（2026-08-27）：

- `open_positions` 13 笔全部未平仓，真实模拟盘盈亏样本 = 0
- `daily_picks` 仅 145 条（2026-08-12 ~ 08-21，约 8 个交易日，5 类榜单）
- 唯一盈亏统计来源是 `strategies/backtest.py` 的信号级回测
  （report.json：布林超卖反弹胜率 39.3%、KDJ 低位金叉 37.0%）
- 策略去留与配额现状：`load_passed_strategies(report.json)` 决定哪些
  信号策略可上榜，`merged_strategy_ratios(new_budget=0.30)` 在达标策略间
  均分 30% 预算，多均线保留 70%

### 需求边界（用户已确认）

| 决策点 | 结论 |
|---|---|
| 自动化程度 | 全自动闭环：标注 → 归因 → 调整 → 门禁验证 → 版本化落库，无人值守 |
| 进化基因 | 仅"策略去留 + 榜单配额"；不动风控参数、不动因子权重 |
| 样本来源 | walk-forward 重放标注为主，真实 picks / live 平仓结果持续混入 |
| 胜负判定 | 止盈止损口径：先触 target 记胜，先触 stop 记负，到期（10 交易日）按收盘价市价定胜负 |
| 节奏 | 每周一次（周五收盘后），独立于每日 picks |

## 2. 架构

新 `evolution/` 包，5 个模块，每个单元职责单一、独立可测：

```
evolution/
  __init__.py
  labeling.py      # walk-forward 重放标注 + 真实持仓自动平仓
  attribution.py   # 纯函数：样本 -> 每策略 {n, win_rate, expectancy, avg_win, avg_loss}
  allocator.py     # 纯函数：归因表 + 现行配置 -> 挑战者配置（去留 + 配额）
  champion.py      # champion-challenger 重放对比、晋级、回滚
  service.py       # 周度闭环编排（evolve 命令的实现）
```

### 与现有代码的接缝（唯一接入点）

`pick_history.py` 的 `build_ma_picks` / `run_picks` 链路中，
`load_passed_strategies()`（report.json）与 `merged_strategy_ratios()`
的调用改为：

1. 优先读 `strategy_config` 表最新 `status='champion'` 版本的
   `active_json`（策略集合）与 `ratios_json`（含多均线基线 70% 与
   信号策略配额）；
2. 表为空或解析失败 → 回退现有 report.json + `merged_strategy_ratios`
   路径，行为与今天完全一致（零回归）。

其余调用方（web_server、plan_builder、高胜率榜）不感知本设计。

## 3. 数据流

```
daily_prices ┐
             ├→ labeling（增量；watermark = 已判定行的最大 date，未判定日期留待下轮补判）→ pick_outcomes
daily_picks  ┘                                          ↑
                                 open_positions 自动平仓（source='live'）

pick_outcomes → attribution → allocator → 挑战者配置
                                  → champion.py 同窗口重放对比
                                    ├ 胜：strategy_config 写入新 champion 版本（生效）
                                    └ 负：写入 status='rejected' 存档（保留学习记录）

次日 run_picks ← strategy_config 最新 champion（active + ratios）
```

### 3.1 重放标注（labeling.py）

对每个历史交易日 D（要求 D 之后存在完整 10 个交易日的价格数据）：

1. 用 `daily_prices` 截至 D 的切片重建市场快照（复用
   `market_data.build_market_from_db` + `strategies.backtest._snapshot`
   的做法）；
2. 检测 D 当日市场状态（复用 `detect_regime`）；
3. 跑与 `run_picks` 同款合并榜单：`merge_candidates` +
   `select_candidates_with_quota(top=20, ratios=现行 champion 配置)`
   （top 与 CI `pick-history 20` 一致；冷启动尚无 champion 时用现行
   report.json + `merged_strategy_ratios` 默认配置）；
4. 每笔 pick 按其 buy/stop/target 与 D 后 10 个交易日的
   high/low/close 判胜负（与 `strategies.backtest._simulate` 同口径：
   盘中先触 target 记胜、先触 stop 记负，两者同日触达记负（保守），
   到期未触发按第 10 日收盘价收益定胜负）；
5. 写入 `pick_outcomes`，`source='replay'`。

回填范围默认最近 250 个交易日（≈1 年，约 250×20 = 5000 样本）。
重放统一使用**现行** champion 配置逐日重跑（历史配置不逐日还原，
归因目标是策略信号质量而非当日运气，且保证配置变更前后可比）。
此后每次 evolve 只标 watermark 之后的新日期（增量，成本 O(新增日)）。

`daily_picks` 中已存在的真实历史 picks（08-12 起）单独回填一遍，
`source='live'`，与未来新增 picks 同路径处理。

### 3.2 live 平仓标注

`open_positions` 中 status='open' 的持仓，每次 evolve 时按同一
止盈止损口径检查 close_date ≤ 最新交易日的数据：命中则以
`source='live'` 写入 `pick_outcomes`。**只写标注表，不改
`open_positions` 本身**（持仓生命周期仍归 plan/trade 模块管）。
已平掉的持仓从 `trade_events` 的 close 事件生成同样记录。

## 4. 进化规则（allocator.py，纯函数）

输入：`attribution`（每策略 n / win_rate / expectancy）+ 现行 champion
配置。输出：挑战者配置或 `NoChange(reason)`。

预算结构不变：多均线基线固定 70%（`DEFAULT_STRATEGY_RATIOS` 等比缩放，
不进化其内部比例），30% 在信号策略间进化。

1. **去留**
   - 在线策略样本 ≥ 30 且（win_rate < 0.35 或 expectancy ≤ 0）→ 下线；
   - 下线策略样本 < 30 → 保留 1 席宽限试跑（ratio 下限对应的最小份额）；
   - 新上线（首次进入）策略同样享受宽限试跑。
2. **配额**：对存活的正期望策略，30% 预算按 expectancy 比例分配；
   单策略上限 15%，在线下限 5%（超限部分按比例回摊给未触限策略）。
   expectancy ≤ 0 但未触发下线条件（样本 <30）的策略不给正配额，
   只占宽限席位。
3. **变更抑制**：挑战者与现行 champion 的比率 L1 距离 ≤ 0.05 →
   返回 `NoChange("周内变化低于噪声阈值")`，不产生挑战者。

## 5. 门禁与回滚（champion.py）

### 晋级（champion → challenger）

在与标注相同的重放窗口内，分别用现行配置与挑战者配置跑榜单
（同一候选池、同一判胜负口径，仅 ratios/active 不同），对比组合级
指标（榜单等权）：

- 挑战者样本 ≥ 100；且
- 挑战者组合胜率 ≥ 现行 + 1.0pp；或胜率差在 ±0.5pp 内且期望严格更高。

胜 → `strategy_config` 追加新版本 `status='champion'`（旧 champion
标记为历史版本，不删除）；负 → `status='rejected'` + reason。

### 自动回滚

每次 evolve 先检查：现行 champion 晋级以来的 **live** 样本
（source='live'）滚动窗口，若近 2 周 live 样本 n ≥ 20 且胜率比
晋级时 `metrics_json` 记录的回放基线胜率低 5pp 以上 →
回退到上一个 champion 版本（当前版本标记 `status='rolled_back'`）。

## 6. 存储（一个迁移文件）

沿用 `db/migrations/*.sql` + `_applied_migrations` 机制，
新增 `db/migrations/2026_08_27_strategy_evolution.sql`：

```sql
-- 实际落库版本（db/migrations/2026_08_27_strategy_evolution.sql）：
-- 存"信号级候选池"而非仅榜单 top-N 行。每个历史日所有被检测到的候选
-- （strategy+code+score+buy/stop/target）都标注入库，这样冠军 vs 挑战者
-- 的门禁评估只需重跑 select_candidates_with_quota（纯选择、无重检测）。
CREATE TABLE IF NOT EXISTS pick_outcomes (
    date        TEXT NOT NULL,   -- 信号日 D
    source      TEXT NOT NULL,   -- 'replay' | 'live'
    code        TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    name        TEXT,
    kind        TEXT,
    score       REAL,
    buy         REAL,
    stop        REAL,
    target      REAL,
    exit_date   TEXT,
    exit_price  REAL,
    outcome_pct REAL,            -- 赢为正、输为负
    win         INTEGER,         -- 1/0，未判定为 NULL
    labeled_at  TEXT,
    PRIMARY KEY (date, source, code, strategy)
);
CREATE INDEX IF NOT EXISTS idx_pick_outcomes_strategy ON pick_outcomes(strategy);
CREATE INDEX IF NOT EXISTS idx_pick_outcomes_date     ON pick_outcomes(date);
```

`strategy_config` 只追加不修改（`rolled_back`/历史标记是唯一例外：
status 列可 UPDATE）。配置生效 = 该表存在 `status='champion'` 的最新版。

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| pick 未来数据不足（退市/停牌/窗口未走完） | `outcome_pct=NULL, win=NULL`，不进统计，不参与门禁 |
| 全部策略样本不足（<30） | evolve 输出"样本不足，不变更"，退出码 0 |
| 挑战者未过门禁 | `status='rejected'` 存档，现行 champion 不变 |
| `strategy_config` 空/JSON 解析失败 | `pick_history` 回退 report.json 旧路径 |
| evolve 中途崩溃 | 落库在单事务内（标注可重跑，UPSERT 幂等；配置提交放最后一步），不产生半成品配置 |
| target/stop 缺失的 pick（如纯观察类） | 不标注入 pick_outcomes（无法按口径判胜负），跳过并计数报告 |

## 8. 入口与调度

```bash
uv run a-finder evolve --db hs300.db                  # 完整闭环
uv run a-finder evolve --db hs300.db --dry-run        # 只打印归因报告+挑战者建议+门禁结果，不写库
uv run a-finder evolve --db hs300.db --backfill-days 250
bash weekly_evolve.sh                                 # cron 每周五收盘后：sync → evolve
```

CLI 注册沿用 `cli_layer.py` 的 subparser 模式。`weekly_evolve.sh`
与 `daily_plan.sh` 平行，复用其 `run_cmd` 包装。

evolve 输出（stdout，人读）：watermark、各策略归因表、去留/配额变更、
门禁对比结果、生效版本号或 NoChange 原因。

## 9. 测试

- `test_evolution_allocator.py`：表驱动纯函数测试 —— 下线阈值（n≥30 &
  胜率<0.35 / 期望≤0）、宽限席位、15% 上限回摊、5% 下限、L1≤0.05
  变更抑制。
- `test_evolution_labeling.py`：合成价格序列三分支 —— 盘中先触 target
  记胜；先触 stop 记负；同日双触记负；第 10 日市价定胜负。
- `test_evolution_champion.py`：fixture outcomes —— 晋级（+1pp）、
  期望晋级路径（胜率 ±0.5pp 内且期望更高）、拒绝（样本<100）、
  live 退化回滚（2 周 -5pp）。
- `test_evolution_migration.py`：两表建表 + 幂等。
- `test_evolution_integration.py`：小 fixture DB 端到端 evolve →
  `pick_history` 读取新 champion ratios；清空 strategy_config →
  回退 report.json 路径。
- 现有测试套件（136 passed）保持全绿。

## 10. 明确不做（YAGNI）

- 分市场状态（bull/sideways/bear）的配额细分 —— live 样本积累出
  分状态统计后按需加。
- 风控参数（RR_TARGET、STOP_ATR_MULT）与多因子权重进化 —— 基因空间
  已确认只含去留+配额。
- Thompson sampling / 遗传规划等更强搜索 —— 周批 + 小基因空间下是负资产。
- web 榜单的进化过程可视化 —— 先用 CLI 输出与 strategy_config 表查证。

## 11. 决策记录

- 为什么方案 A 而非贪心/bandit：基因空间只有 ~7 个策略 × 每周一小格
  配额，比例分配 + 门禁已覆盖有效区域；额外搜索能力在小样本上表现为
  过拟合而非收益。
- 为什么重放为主：真实模拟盘 0 平仓，纯等 live 数据闭环无法冷启动；
  重放用当时的榜单真实构成 + 事后真实价格出场，与 live 口径同构，
  两者可在同一统计表分层（source 列）对比校准。
- 为什么基线 70% 不动：均线榜单是现有主榜，进其内部权重等于同时改两
  个变量，破坏挑战者对比的可归因性。

## 实现期偏差（相对本文档初稿）

1. **候选池持久化到信号级**：`pick_outcomes` 每库存当天所有被检测到的候选
   （含 score），而非仅榜单 top-N。门禁评估因此在同一份数据上重跑选择即可，
   无需按挑战者配置重新检测。
2. **top 默认 20**（与 CI `pick-history 20` 对齐），非初稿的 10。
3. **增量水位线只看已判定行**（`win IS NOT NULL`）：尾部不足 10 个交易日的
   日期本轮不落库，下周自动补判，避免 NULL 行堆积与水位线越界。
4. **重放用逐股日期索引**（date→idx），不再受 `run_strategy_backtest` 的
   `min(len)` 全局截断影响——一只短历史成分股不会把重放窗口压到 40 天。
5. **`select_candidates_with_quota` 回填修复**：空席位不再回填给在 ratios 中
   显式配 0 的策略，否则进化压制的策略会借回填复活、门禁失真。未声明 key
   （如均线自适应家族的市况命名）维持原弹性行为。
