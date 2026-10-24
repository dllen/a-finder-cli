# 可转债策略 — 多因子打分 + 每日榜单 + 退出预警

**日期**：2026-10-24
**状态**：设计中（待批准后实施）

---

## 一、目标

新增一个**可转债（可转换公司债券）**策略模块，覆盖三类目标：

- **债底防守**：低价格 + 高纯债价值保护 + 有票息 + 高评级
- **股性进攻**：低转股溢价率 + 高平价度 + 跟涨弹性 + 正股动量
- **条款博弈**：离强赎远、离回售远、有下修历史

三组因子合成为单一分数，输出**每日榜单**；配合持仓表输出**退出预警**（强赎逼近 / 回售触发 / 跌破止损 / 止盈）。

**非目标（本期明确排除）**：

- ❌ **转债回测** — 历史行情无可靠全量源（见 §二），用户已确认第一期不做
- ❌ Web 页面集成（CLI 优先，后续可加）
- ❌ 自动纸面交易（只记录持仓 + 预警）
- ❌ 正股基本面因子（PE/PB/财务质量，需额外同步数据源）
- ❌ 盘中实时盯盘（需常驻进程）

---

## 二、数据源探明结论（设计依据）

以下结论均为**本机实测**（2026-10-24），是本设计的数据选型依据。

| 数据需求 | 可用源 | 实测结果 |
|---|---|---|
| 全市场清单 | `ak.bond_zh_cov()` | ✅ 稳定返回 1065 只，含正股价/转股价/转股价值/转股溢价率/债现价/信用评级/发行规模。**但含未上市新券与已退市券**，仅约 320 只有完整实时价 |
| 单只全量条款 | `ak.bond_zh_cov_info(symbol=code)` | ✅ 72 字段，含到期日、纯债价值(PBV_RATIO)、转股价、转股价值、转股溢价率、评级、赎回条款文本、下修记录 |
| 强赎状态 | `ak.bond_cb_redeem_jsl()` | ✅ 319 只，含强赎天计数、强赎状态、最后交易日、最后交易��� |
| JSL 全字段实时 | `ak.bond_cb_jsl()` | ⚠️ **只返回双低最低的 30 只**，不是全市场（实测无论 `rp` 设 50/500、翻页参数如何变化，返回集恒为同样 30 只） |
| 东财比价表 | `ak.bond_cov_comparison()` | ⚠️ 字段最全（含回售触发价/强赎触发价/到期赎回价/开始转股日），但**实测已把本机 IP 封禁**（连续多次 `ConnectionError`，60s 冷却后仍失败，`push2`/`7.push2`/`82.push2`/`48.push2`/`push2his` 全部主机均失败） |
| 历史 K 线 | 新浪 `bond_zh_hs_cov_daily` / 腾讯 `fqkline` | ❌ **数据被截断**：`113050` 停在 2025-07-14，`123110` 停在 2022-11-22，`110059` 停在 2025-10-22 — 每只债在不同随机日期断掉 |
| JSL 历史 | `jisilu.cn/data/cbnew/history/` | ❌ 反爬不稳定（200 后随即失败），且被上游截断 |

**结论**：没有单一数据源同时满足「全市场 + 全字段 + 稳定」。必须做**多源组合 + 分层降级**。

**方案选型记录**：

- 方案 A（多源合并 + 分层降级）— **采纳**。以 `bond_zh_cov()` 为宇宙主源，`bond_zh_cov_info()` 补条款，`bond_cb_redeem_jsl()` 补强赎。
- 方案 B（东财比价表为主）— **不采纳为主源**（IP 封禁反复发生，见 `docs/TODO.md` 记录）。可作为**可选加速通道**集成。
- 方案 C（精简版只算双低）— **不采纳**。拿不到纯债价值 / 到期日 / 强赎状态，等于放弃债底与条款两个目标。

---

## 三、整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  数据源层 (cb_data_provider.py — 新增)                          │
│  ┌───────────────┐ ┌────────────────┐ ┌────────────────────┐ │
│  │ ak.bond_zh_cov │ │ ak.bond_zh_cov │ │ ak.bond_cb_redeem  │ │
│  │  全市场清单    │ │  _info 补全    │ │  _jsl 强赎状态     │ │
│  │  (1065 只)    │ │  (纯债价值/    │ │  (强赎天计数/状态) │ │
│  │               │ │   到期日)      │ │                    │ │
│  └───────┬───────┘ └───────┬────────┘ └─────────┬──────────┘ │
│          └─────────────────┴────────────────────┘            │
│                   retry_call 包装（复用 utils.retry_call）       │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│  存储层（SQLite，独立表，不污染股票体系）                        │
│  cb_universe   : 每日全市场快照（基础字段）                      │
│  cb_factors    : 粗筛候选的补全字段                              │
│  cb_picks      : 每日榜单结果                                    │
│  cb_positions  : 持仓（供退出预警用）                            │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│  策略层 (strategies/convertible.py — 新增)                      │
│  粗筛 → 补全 → 因子打分 → 排序 → 输出榜单                       │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
┌──────────────────────────────────────────────────────────────┐
│  CLI 层                                                        │
│  cb-picks   : 生成今日榜单        cb-alerts  : 持仓退出预警     │
│  cb-add     : 登记持仓            cb-positions : 查看持仓       │
└──────────────────────────────────────────────────────────────┘
```

---

## 四、隔离原则

**完全隔离，不改动现有股票体系**：

- 新表前缀 `cb_`，不复用 `daily_picks` / `trade_plan` / `open_positions`
- 新模块 `cb_data_provider.py`、`strategies/convertible.py`
- **不修改** `plan_builder.py` / `risk_manager.py` / `shared_lib/strategy.py` / 现有 CLI 子命令
- `config.py` 只**新增**区块，不改现有股票常量

**理由**：转债的整手单位是 10 张（面值 100 元）= 1000 元，与股票的 100 股 = 100 元逻辑完全不同；转债 T+0 交易、无涨跌幅限制、有强赎/回售条款。混入现有 plan 会污染既有回测与纸面交易。

---

## 五、Schema 迁移

新文件：`db/migrations/2026_10_24_convertible_bond.sql`（符合项目约定：新表走迁移文件，不动 `db_schema.py`）。

```sql
CREATE TABLE IF NOT EXISTS cb_universe (
    trade_date     TEXT NOT NULL,
    code           TEXT NOT NULL,      -- 转债代码
    name           TEXT,
    stock_code     TEXT,               -- 正股代码
    stock_name     TEXT,
    price          REAL,               -- 债现价
    convert_price  REAL,               -- 转股价
    convert_value  REAL,               -- 转股价值
    premium_rate   REAL,               -- 转股溢价率 (%)
    stock_price    REAL,               -- 正股价
    rating         TEXT,               -- 信用评级
    issue_size     REAL,               -- 发行规模（亿元）
    listed         INTEGER DEFAULT 0, -- 1=已上市
    updated_at     TEXT,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS cb_factors (
    trade_date   TEXT NOT NULL,
    code         TEXT NOT NULL,
    bond_floor   REAL,      -- 纯债价值
    ytm          REAL,      -- 到期税前收益 (%)
    year_left    REAL,      -- 剩余年限（年）
    redeem_dist  REAL,      -- 强赎触发距离 (%)
    put_dist     REAL,      -- 回售触发距离 (%)
    adj_count    INTEGER,   -- 历史下修次数
    redeem_status TEXT,     -- 强赎状态文本
    factor_json  TEXT,      -- 各因子原始值 + 得分明细
    updated_at   TEXT,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS cb_picks (
    trade_date   TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT,
    price        REAL,
    total_score  REAL,
    a_score      REAL,      -- 债底组
    b_score      REAL,      -- 股性组
    c_score      REAL,      -- 条款组
    size_pct     REAL,
    shares       INTEGER,   -- 张数（10 的整数倍）
    stop_price   REAL,
    tp_price     REAL,
    reason       TEXT,      -- 入选理由
    risk_note    TEXT,      -- 风险提示
    factor_counts TEXT,     -- 各组实际参与的因子数 JSON（缺失因子不计分时 < 预期数）
    updated_at   TEXT,
    PRIMARY KEY (trade_date, rank)
);

CREATE TABLE IF NOT EXISTS cb_positions (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    stock_code  TEXT,
    entry_date  TEXT,
    entry_price REAL,
    shares      INTEGER,     -- 张
    stop_price  REAL,
    tp_price    REAL,
    note        TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cb_picks_date ON cb_picks(trade_date);
CREATE INDEX IF NOT EXISTS idx_cb_universe_code ON cb_universe(code);
```

---

## 六、数据源降级链

每类因子有明确取数顺序。**缺失时该因子不参与打分**（并在对账输出标记），而不是整个流程失败。

| 因子 | 主源 | 备源 | 缺失处理 |
|---|---|---|---|
| 基础行情（价/转股价/溢价率/正股价） | `bond_zh_cov()` | `bond_cov_comparison()` | 跳过该债 |
| 信用评级 | `bond_zh_cov()` | `bond_zh_cov_info()` 的 `RATING` | 评分取中性 60 |
| 纯债价值 `bond_floor` | `bond_zh_cov_info()` 的 `PBV_RATIO` | — | 该因子不计分 |
| 到期日 → `year_left` | `bond_zh_cov_info()` 的 `EXPIRE_DATE` | — | 该因子不计分 |
| 到期收益 `ytm` | 自算（到期赎回价/现价 年化） | `bond_cb_jsl()` 的「到期税前收益」（仅覆盖 30 只） | 该因子不计分 |
| 强赎触发价 | `bond_zh_cov_info()` 的 `REDEEM_TRIG_PRICE` | 转股价 ×1.3 估算 | 用估算值 |
| 回售触发价 | `bond_zh_cov_info()` 的 `RESALE_TRIG_PRICE` | 转股价 ×0.7 估算 | 用估算值 |
| 强赎状态 / 天计数 | `bond_cb_redeem_jsl()` | `bond_zh_cov_info()` | 视为「未知」，不触发硬剔除 |
| 下修次数 `adj_count` | 无可靠源 | — | 取中性 50（见 §十五-3） |

**主源全部失败** → 读当日 `cb_universe` 缓存快照兜底，并输出告警。

---

## 七、Pipeline：粗筛 → 补全

### 阶段 1：全市场快照（1 次请求）
`ak.bond_zh_cov()` → 1065 只。写入 `cb_universe`。

### 阶段 2：粗筛（纯本地，无网络）

剔除规则（可配）：

```
剔除  上市时间为空（NaT）—— 实测 1065 只中 7 只为 NaT，均为未上市新券
剔除  信用评级 ∈ {CC, C, CCC} 或评级字符串含 'B'
剔除  转股溢价率 > 40 或 < -5
剔除  债现价 < 80 或 > 200
剔除  正股价 <= 0 或 转股价 <= 0
剔除  剩余规模 < 0.5 亿元
```

**未上市判据**：`上市时间` 字段为 `NaT` 即未上市（实测：1065 只中 1058 只有上市时间，7 只 NaT）。这 7 只的 `债现价` 恒为 100.0（发行价），可作为二次校验。

预期剩余 ~100-150 只。

### 阶段 3：逐只补全（~100-150 次请求）
`ak.bond_zh_cov_info(symbol=code)` → 纯债价值、到期日、下修次数、评级。写入 `cb_factors`。

**akshare 字段名映射**（`bond_zh_cov_info` 返回单行 DataFrame，字段名为英文）。以下映射已在 `127120`（新券）与 `113059`（活跃老券）上实测验证：

| 目标字段 | akshare 列名 | 实测校验 |
|---|---|---|
| `bond_floor` 纯债价值 | `PBV_RATIO` | ✅ 127120=2.93, 113059=1.01 |
| 到期日 | `EXPIRE_DATE` | ✅ `'2032-10-27 00:00:00'` / `'2028-05-20 00:00:00'` |
| 评级 | `RATING` | ✅ `'AA'` |
| 强赎触发价 | `REDEEM_TRIG_PRICE` | ✅ 8.35 / 54.03（= 转股价 ×1.3） |
| 回售触发价 | `RESALE_TRIG_PRICE` | ✅ 4.49 / 29.09（= 转股价 ×0.7） |
| 转股价 | `TRANSFER_PRICE` | ✅ 6.42 / 41.56 |
| 正股价 | `CONVERT_STOCK_PRICE` | ✅ 6.27 / 9.17 |
| 转股价值 | `TRANSFER_VALUE` | ✅ 97.6636 / 22.0645 |
| 债现价 | `CURRENT_BOND_PRICE` | ⚠️ 127120 返回 `'-'`（未上市），113059=115.315 |
| 转股溢价率 | `TRANSFER_PREMIUM_RATIO` | ✅ 2.39 / 422.63 |

**重要实测发现**：已强赎退市的债（如 `113050`）上述多个字段返回 `None`（`REDEEM_TRIG_PRICE` / `TRANSFER_PRICE` / `TRANSFER_VALUE` / `CURRENT_BOND_PRICE` 全空），因为已终止转股。**这类债应被 `bond_cb_redeem_jsl()` 的强赎状态识别并剔除**，正好与硬剔除规则 1 吻合。

**字段缺失一律走「该因子不计分」路径**，不抛异常。`CURRENT_BOND_PRICE` 返回 `'-'`（字符串）需转 float 时容错。

**调用格式（实测）**：`ak.bond_zh_cov_info(symbol=...)` 接受**裸 6 位代码**，不需要 `sh`/`sz` 前缀：

```
bond_zh_cov_info(symbol='113059')  -> OK (福莱转债)
bond_zh_cov_info(symbol='sh113059') -> FAIL (TypeError: 'NoneType' object is not subscriptable)
```

> 注意这与本项目其他 akshare 调用不同（`akshare_data_provider.fetch_daily_kline_akshare` 会加 `sh`/`sz` 前缀）。转债接口**不可**加前缀。
>
> 另外 `ak.bond_zh_hs_cov_daily`（历史 K 线）**需要**前缀（`sh113059`），与 `bond_zh_cov_info` 相反。两者勿混用。

**限速**：`concurrency=4`、`rate=6/s`（比股票同步更保守，`bond_zh_cov_info` 是单只请求，易触发东财限流）。

**容错**：单只失败 → 该债降级评分，不中断整批。复用 `utils.retry_call`（指数退避 + 抖动，4xx 不重试）。

### 阶段 4：强赎状态合并（1 次请求）
`ak.bond_cb_redeem_jsl()` → 与阶段 3 结果按 code 合并。

---

## 八、因子与打分模型

所有因子先转成 **0-100 分**（越高越好），组内加权合成三组分，三组再加权成总分。

**缺失因子的组按剩余因子重新归一化权重**，而不是填 0（避免「缺数据 = 差」）。

**组内因子权重**：A 组 5 因子等权（各 0.2），B 组 4 因子等权（各 0.25），C 组 3 因子等权（各 1/3）。某因子缺失时，剩余因子按原比例重新归一化。

**整组缺失的退化规则**：若某组一个可用因子都没有，该组得分记为 `None`（不参与总分），总分按剩余可用组的权重重新归一化。若**三组全空**，该债不进入榜单（无法评分）。

**最低可评分门槛**：至少 2 个可用因子方可进入榜单。低于此数说明数据源大面积失败，输出告警而非给出可疑排名。

### A 组：债底防守（权重 40%）

| 因子 | 计算 | 打分规则 | 缺失 |
|---|---|---|---|
| `bond_floor` | 纯债价值 / 债现价 | ≥1→100；≥0.95→90；≥0.9→75；≥0.85→60；≥0.8→40；<0.8→20 | 不计分 |
| `price_level` | 债现价 | ≤90→100；≤100→85；≤110→70；≤120→55；≤130→40；>130→20 | 不计分 |
| `ytm` | 到期税前收益 % | ≥3→100；≥2→85；≥1→70；≥0→55；>-1→35；<-1→15 | 不计分（**需自算**，见下） |
| `rating` | 评级 | AAA=100, AA+=95, AA=85, AA-=70, A+=50, A=35；含 B/CC→0 并直接剔除 | 取 60 |
| `year_left` | 到期日−今天（年） | 3-6→100；2-3→80；1-2→60；>6→60；<1→30 | 不计分 |

**`ytm` 与 `year_left` 的取数说明（实测澄清）**：

`ak.bond_zh_cov()`（全市场主源）的 19 个字段为：`债券代码, 债券简称, 申购日期, 申购代码, 申购上限, 正股代码, 正股简称, 正股价, 转股价, 转股价值, 债现价, 转股溢价率, 原股东配售-股权登记日, 原股东配售-每股配售额, 发行规模, 中签号发布日, 中签率, 上市时间, 信用评级` — **不含到期日与到期收益**。

因此：

- `year_left` 由 `bond_zh_cov_info()` 的 `EXPIRE_DATE` 计算（`EXPIRE_DATE` 已实测可用）
- `ytm` **没有现成字段**，需自算：
  ```
  到期赎回价 默认取面值×1.10（按主流条款），来自 bond_zh_cov_info 的到期赎回价字段
  ytm = ((到期赎回价 / 债现价) ** (1 / year_left) - 1) * 100
  ```
  `year_left <= 0`（已到期）时 `ytm` 不计分。
  - 备选路径：`ak.bond_cb_jsl()` 提供现成的「到期税前收益」字段，但**只覆盖双低最低的 30 只**，覆盖不足，故不作为主路径。
  - 若 `bond_zh_cov_info` 能提供更精确的到期赎回价，则优先使用；否则用 1.10 系数估算并在 `factor_json` 中标记 `ytm_estimated=1`。

**A 组目标画像**：债现价 90-105、溢价率 10-25%。

### B 组：股性进攻（权重 30%）

| 因子 | 计算 | 打分规则 | 缺失 |
|---|---|---|---|
| `premium` | 转股溢价率 % | ≤5→100；≤10→90；≤15→75；≤20→60；≤30→40；≤50→20；>50→0 | 不计分 |
| `convert_ratio` | 转股价值/100 | ≥110→100；≥100→85；≥95→70；≥85→50；<85→30 | 不计分 |
| `elasticity` | 债涨跌幅/股涨跌幅 | ≥1.0→100；≥0.8→75；≥0.6→50；<0.6→25 | 由溢价率倒推 |
| `stock_momentum` | 正股近 20 日涨幅 | ≥15→100；≥8→80；≥0→55；≥-10→30；<-10→15 | 不计分（**第一期必然缺失**，见 §十五-5） |

**B 组目标画像**：债现价 110-130、溢价率 5-15%。

### C 组：条款与风险（权重 30%）

| 因子 | 计算 | 打分规则 | 缺失 |
|---|---|---|---|
| `redeem_dist` | (强赎触发价/正股价−1)×100 | ≥40→100；≥25→85；≥15→70；≥8→50；≥3→30；<3→0 并剔除 | 转股价×1.3 估算 |
| `put_dist` | (回售触发价/正股价−1)×100 | ≥-20→100；≥-35→80；≥-50→60；≥-65→40；<-65→20 | 转股价×0.7 估算 |
| `adj_count` | 历史下修次数 | 0→50；1→80；2→100；≥3→95 | 取 50 |

**C 组说明**：`adj_count` 与得分**正相关** — 多次下修说明公司有强烈促转股动机。

### 合成公式

```
A组分 = Σ(A组因子分 × 组内归一化权重)
B组分 = Σ(B组因子分 × 组内归一化权重)
C组分 = Σ(C组因子分 × 组内归一化权重)

总分 = w_a×A组分 + w_b×B组分 + w_c×C组分
```

权重存放于 `config.py` 新增区块 `CB_SCORING_WEIGHTS = {"a": 0.40, "b": 0.30, "c": 0.30}`，CLI 支持 `--weights a=0.5,b=0.25,c=0.25` 临时覆盖。

### 为何不复用 `MultiFactorBase`

`MultiFactorBase` 用 z-score 归一化，隐含正态分布假设。转债因子严重偏态（债现价集中在 80-130），z-score 会过度放大极端值。分档打分可解释（「溢价率 ≤10% 得 90 分」）、易调参、易向用户说明。

---

## 九、硬性剔除（不进入打分）

这些是**风险闸门**而非评分项 — 命中即出局，因为其损失是本金永久性级别的：

1. 已公告强赎（`redeem_status` 含「已公告强赎」）→ 必须卖，不买
2. 强赎触发距离 < 3%
3. 信用评级含 B / CC / C
4. 剩余规模 < 0.5 亿元
5. 正股为 ST / *ST
6. 转股价值 < 60（深度价外，转股无望）
7. 债现价 < 80 或 > 200

### 与粗筛的关系（避免两处规则漂移）

粗筛（§七 阶段 2）与硬剔除（§九）**共用同一组常量**，实现上单点定义：

| 规则 | 粗筛阶段 | 硬剔除阶段 | 说明 |
|---|---|---|---|
| 未上市（上市时间 NaT） | ✓ | — | 粗筛即排除，硬剔除无需重复 |
| 评级含 B/CC/C | ✓ | ✓ | 同一常量 `CB_EXCLUDE_RATINGS` |
| 溢价率 >40 或 <−5 | ✓ | — | 粗筛即排除 |
| 债现价 <80 或 >200 | ✓ | ✓ | 同一常量 `CB_PRICE_MIN` / `CB_PRICE_MAX` |
| 正股价/转股价 ≤0 | ✓ | — | 粗筛即排除 |
| 剩余规模 <0.5 亿 | ✓ | ✓ | 同一常量 `CB_MIN_ISSUE_SIZE` |
| 已公告强赎 | — | ✓ | 需要阶段 4 的强赎数据，粗筛时尚不可得 |
| 强赎距离 <3% | — | ✓ | 需要阶段 3/4 的触发价，粗筛时仅能估算 |
| 正股 ST/*ST | — | ✓ | 需正股名称判定 |
| 转股价值 <60 | — | ✓ | 需转股价值，粗筛阶段已有 `bond_zh_cov()` 的该字段，故可在粗筛一并执行 |

**结论**：两阶段规则的并集是最终存活集合。实现上把「粗筛可判定」的规则集中在 `prefilter()`，「需补全数据」的规则集中在 `apply_hard_exclusions()`，共享同一批常量定义于 `config.py` 的 `CB_FILTERS` 区块。

> 注：转股价值 <60 这条在粗筛阶段即可判定（`bond_zh_cov()` 已含 `转股价值`），为避免遗漏，实现时归入 `prefilter()`。

---

## 十、仓位与风控

`config.py` 新增区块（**只新增，不修改现有股票常量**）：

```python
# === 可转债策略（独立于股票体系）===
CB_SCORING_WEIGHTS = {"a": 0.40, "b": 0.30, "c": 0.30}   # 三组权重

CB_FILTERS = {
    "premium_min": -5.0,        # 转股溢价率下限 (%)
    "premium_max": 40.0,        # 转股溢价率上限 (%)
    "price_min": 80.0,          # 债现价下限
    "price_max": 200.0,         # 债现价上限
    "min_issue_size": 0.5,      # 剩余规模下限（亿元）
    "min_convert_value": 60.0,  # 转股价值下限
    "redeem_dist_min": 3.0,     # 强赎触发距离下限 (%)
    "exclude_ratings": ("B", "CC", "C"),  # 评级含这些子串即剔除
    "min_factors": 2,           # 最少可用因子数，低于则不进入榜单
}

CB_RISK = {
    "max_single": 0.15,       # 单只上限 15%
    "max_positions": 10,      # 最多 10 只
    "stop_loss_pct": 0.08,    # 止损 -8%
    "take_profit_pct": 0.20,  # 止盈 +20%
    "lot_size": 10,           # 整手：10 张 = 面值 1000 元
    "min_price": 80.0,
    "max_price": 200.0,
}
```

> `CB_FILTERS` 中的 `price_min`/`price_max` 与 `CB_RISK` 中的 `min_price`/`max_price` 语义相同（同一个价格区间），实现时以 `CB_FILTERS` 为准，`CB_RISK` 侧引用之，避免两处漂移。

**与股票风控的差异**：

| 项 | 股票 | 转债 | 理由 |
|---|---|---|---|
| 整手 | 100 股 | 10 张 | 面值 100 元/张 |
| 止损 | ATR×2 动态 | 固定 -8% | 转债有纯债价值托底，ATR 止损在债底附近会频繁被扫；但也不能用 -20%（会吃到强赎前深跌） |
| 止盈 | RR=2.0 目标 | +20% 或触发强赎 | 转债无明确目标位，+20% 为经验阈值 |
| 持仓数 | 8 | 10 | 转债单只波动大，需更高分散度 |

**张数计算**：

```
可买张数 = floor( 资金 × size_pct / (现价 × 10) ) × 10
不足 1 手（<10 张）→ 该债跳过
```

**硬退出条件**（优先级高于止盈止损）：

1. 已公告强赎 → **立即清仓**（最常见的「赚了 20% 却归零」）
2. 正股触发回售条件且公司公告回售 → 评估在回售价卖出
3. 距强赎触发价 < 3% → 主动减仓

---

## 十一、退出预警（`cb-alerts`）

每日收盘后扫描 `cb_positions`：

| 级别 | 触发条件 | 建议动作 |
|---|---|---|
| 🔴 紧急 | 已公告强赎；或强赎距离 < 3% | 立即清仓 |
| 🟠 重要 | 跌破止损价；或进入回售区（正股 < 转股价×0.7） | 减仓 / 评估回售 |
| 🟡 关注 | 距强赎 3-8%；或溢价率 > 50%（估值透支） | 止盈部分 |
| ⚪ 常规 | 达到止盈目标 | 分批止盈 |

输出：CLI 表格 + 一条可读文本（便于贴群 / 存笔记）。支持 `--json`。

**第一期只记录持仓，不自动交易** — 转债 T+0 与强赎事件的时点风险，自动纸面交易会给出错误的安全感。

---

## 十二、CLI 接口

```bash
# 生成今日榜单
uv run a-finder cb-picks --top 20
uv run a-finder cb-picks --top 20 --dry-run              # 只打印，不落库
uv run a-finder cb-picks --top 20 --weights a=0.5,b=0.25,c=0.25
uv run a-finder cb-picks --top 20 --min-premium 5 --max-premium 30

# 持仓与预警
uv run a-finder cb-add 113059 --shares 50 --price 108.5
uv run a-finder cb-positions
uv run a-finder cb-alerts
uv run a-finder cb-alerts --json
```

新增 4 个子命令，不改动现有任何子命令。风格对齐现有 `linyuan-picks`（argparse + `formatter.format_table`）。

### 榜单输出格式

```
# 转债榜单 2026-10-24  (活跃 138 只, 硬性剔除 12 只, 候选 45 只)
排名 代码     名称       现价    转股价  溢价率  债底   强赎距  总分   A    B    C   建议
1    113059  华安转债   108.50  12.30   8.21   96.2   22.4%  87.3  82   90   88  买
2    128036  金农转债   118.20  8.50    12.44  101.3  18.9%  84.1  90   72   85  买
```

每只债附**入选理由**（自动生成，如「债底 96.2 保护充分 + 溢价率 8.2% 偏低 + 距强赎 22.4% 安全」）与**风险提示**（如「剩余规模 0.8 亿偏小」）。

---

## 十三、测试策略

新建 `tests/test_convertible.py`（TDD），全部 mock，不打网络：

1. `test_prefilter_excludes_st_stock` — ST 正股被剔除
2. `test_prefilter_excludes_low_rating` — CC 评级被剔除
3. `test_prefilter_excludes_unlisted` — 上市时间为空的被剔除
4. `test_prefilter_excludes_extreme_premium` — 溢价率 45% 被剔除
5. `test_score_bond_floor_above_par` — 纯债价值 > 面值得满分
6. `test_score_premium_buckets` — 溢价率分档边界值正确
7. `test_missing_factor_renormalizes_group` — 缺因子的组按剩余因子重新归一化，不填 0
8. `test_hard_exclusion_redeem_announced` — 已公告强赎直接出局
9. `test_redeem_distance_below_3pct_excluded` — 强赎距离 < 3% 出局
10. `test_position_sizing_uses_lot_of_10` — 张数按 10 的整数倍
11. `test_sizing_skips_if_less_than_one_lot` — 资金不够 1 手则跳过
12. `test_alert_emergency_on_redeem_announced` — 强赎预警为 🔴 紧急
13. `test_alert_stop_loss_triggered` — 跌破止损触发 🟠
14. `test_migration_creates_cb_tables` — 迁移建出 4 张表
15. `test_provider_falls_back_when_primary_fails` — 主源失败走备源
16. `test_provider_degrades_when_all_sources_fail` — 全源失败时读缓存快照并告警

目标：测试 **+16**，全量套件不回归。

---

## 十四、实施顺序（TDD）

1. 迁移 + `cb_data_provider.py`（含降级逻辑）→ 测试 13/14/15/16
2. 粗筛 + 因子打分（`strategies/convertible.py`）→ 测试 1-9
3. 仓位计算 + `cb_positions` → 测试 10/11
4. 退出预警 → 测试 12/13
5. CLI 4 个子命令
6. README / CLAUDE.md 文档更新
7. 真实数据跑通验证（`cb-picks --top 20`）

---

## 十五、风险与开放问题

1. **`ak.bond_zh_cov()` 覆盖范围** — 该接口返回的是「可转债发行列表」，含未上市新券。粗筛需依赖「上市时间非空」判定已上市。**首跑必须验证**该字段的实际格式与空值分布。
2. **`bond_zh_cov_info` 字段稳定性** — 已实测：活跃债（`127120`/`113059`）的 `PBV_RATIO`/`EXPIRE_DATE`/`REDEEM_TRIG_PRICE`/`RESALE_TRIG_PRICE` 均有值；但**已强赎退市的债**（如 `113050`）多个字段返回 `None`，且未上市新券的 `CURRENT_BOND_PRICE` 返回字符串 `'-'`。需做空值与类型容错。
3. **下修次数来源不足** — `ak.bond_cb_adj_logs_jsl()` 实测只返回 3 条（仅「洪涛转债」历史），覆盖不全。`adj_count` 大多情况下将走中性值 50。**若要提升准确度**，需从 `bond_zh_cov_info()` 的 `RESALE_CLAUSE` / 条款文本解析，或自建下修事件表（后续迭代）。
4. **`ak.bond_cov_comparison()` 备源可用性** — 已实测被封 IP。作为备源仅在主源失败时尝试，且失败即静默降级（不阻塞流程）。
5. **正股 20 日动量 — 第一期必然缺失** — 需要正股历史 K 线。现有 `hs300.db` 只覆盖沪深300成分股，而转债正股含大量创业板/科创板非成分股（实测转债正股 002550/002887/688510 等均非沪深300成分）。因此 `stock_momentum` 在第一期**对所有转债都不计分**，B 组由其余 3 个因子（premium / convert_ratio / elasticity）构成，权重在组内重新归一化。
   - **后续选项**：(a) 扩展正股同步范围到转债正股全集（约 500 只额外）；(b) 改用单点数据源的近似（如转股价值变化率）；(c) 保持缺失状态。
   - **本期决策**：(c) 保持缺失，并在榜单输出中显式提示「B 组基于 3/4 因子」。实现时 `cb_picks` 增列记录每组实际参与的因子数（`factor_counts` JSON），避免误读分数。
6. **弹性因子近似** — `elasticity` 用当日涨跌幅比近似，不是严格的弹性回归。仅作排序参考，非精确度量。
7. **首次运行耗时** — 阶段 3 的 ~100-150 次单只请求，按 6/s 限速约 20-30 秒；加主源请求总计约 40-60 秒。可接受。

---

## 十六、评审 / 实施

- 本文档已获用户批准（三节设计逐节确认）
- 接着调 `writing-plans` skill 拆实施计划
- 实施按 TDD：先测试 → 迁移 → 数据源 → 粗筛打分 → 仓位 → 预警 → CLI → 文档
- 不引入新依赖（akshare / pandas / sqlite 已有）
