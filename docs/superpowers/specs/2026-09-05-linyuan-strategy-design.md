# 林园选股策略 — 财务质量主导（连续 5 年）

**日期**：2026-09-05
**状态**：设计中（待批准后实施）

---

## 一、目标

新增一个**财务质量主导**的多因子策略（沿用 `pharma_multi_factor` / `dividend_multi_factor` 模板），核心条件是：

- **行业白名单**：医药 + 中药 + 食品饮料 + 高端制造（沿用 `hs300_metadata.industry`）
- **连续 5 年 毛利率 > 40%**
- **连续 5 年 扣非净资产收益率 > 15%**
- **哲学**：垄断 + 成瘾（不写进算法，纯靠上面两个数字间接体现）
- 财务为主，不叠加技术信号

**非目标**：
- 不做组合优化（与 pharma 一致，等权 + top_n 上限）
- 不做实时盘中监控（择时不在本策略范围）
- 不引入新财务指标（毛利率 / 扣非 ROE 足够定锚）

---

## 二、整体架构

```
┌────────────────────────────────────────────────────────────────┐
│                  数据底座（一次性补齐）                            │
├────────────────────────────────────────────────────────────────┤
│  sync-industry         → 回填 hs300_metadata.industry            │
│  sync-fundamentals-history → 新增 fundamentals_history 表         │
│           ↓                                                     │
│          sqlite (DB)                                            │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│                  策略层（新增）                                    │
├────────────────────────────────────────────────────────────────┤
│  LinYuanMultiFactor (继承 MultiFactorBase)                      │
│    _filter_candidates：                                          │
│      1) 行业 ∈ {医药/中药/食品饮料/高端制造}（字符串白名单）        │
│      2) 连续 5 年 (gross_margin > 0.40 ∧ roe_excl > 0.15)         │
│    select → 等权 + top_n 截断                                    │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│                  CLI 暴露                                          │
├────────────────────────────────────────────────────────────────┤
│  uv run a-finder linyuan-picks --top 20                          │
│  uv run a-finder picks --strategy linyuan                        │
└────────────────────────────────────────────────────────────────┘
```

---

## 三、Schema 迁移

新文件：`db/migrations/2026_09_05_fundamentals_history.sql`

```sql
CREATE TABLE IF NOT EXISTS fundamentals_history (
    code TEXT NOT NULL,
    year INTEGER NOT NULL,                -- 公历年度（年报对应年度）
    gross_margin REAL,                    -- 毛利率 (%)
    roe_excl REAL,                        -- 扣非净资产收益率 (%)
    revenue REAL,                         -- 营业总收入（元，可选）
    net_profit_excl REAL,                 -- 扣非归母净利润（元，可选）
    report_date TEXT,                     -- 实际披露日期 2024-04-30
    synced_at TEXT,
    PRIMARY KEY (code, year)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_history_code
    ON fundamentals_history(code);
```

**约束**：缺失年份留空（不写 0/NaN），便于"连续 5 年"判断时跳过。

---

## 四、akshare 拉年报

akshare `stock_financial_abstract(symbol=code)` 已经按列返回多期数据，但要识别**年报列**（报告期 = 12-31 或名称含"年报"/"年度报告"）。

新增 `akshare_data_provider.py::_annual_metrics(df) -> Dict[int, dict]`：

```python
def _annual_metrics(df: pd.DataFrame) -> Dict[int, dict]:
    """从 stock_financial_abstract DataFrame 抽年报（按 12-31 / 12-30 报告期）。"""
    out: Dict[int, dict] = {}
    if df is None or df.empty:
        return out
    # 列名格式约定：最新报告期在最左；按需从已读代码样例确认
    for col in df.columns:
        # 提取年份；只接受 12 月披露的列
        m = re.match(r"^(\d{4})-12-\d{2}", str(col))
        if not m:
            continue
        year = int(m.group(1))
        out[year] = {
            "gross_margin": _first_valid(_abstract_metric(df, "毛利率", col=col)),
            "roe_excl": _first_valid(_abstract_metric(df, "净资产收益率(扣非)", col=col)),
        }
    return out
```

**风险**：akshare 列名格式可能含「2024-03-31」季度 /「2024-12-31」年度；首跑前必须离线验证一两只样本股。`_abstract_metric` 当前签名是按行取整列，需扩展加 `col=` 参数。

`fetch_fundamentals_history_akshare(code)` 新函数：

```python
def fetch_fundamentals_history_akshare(code: str) -> List[FundamentalsHistoryRow]:
    """单只股票拉历年财报关键指标；任何 akshare 异常返回 []。"""
    try:
        df = ak.stock_financial_abstract(symbol=code)
    except Exception:
        return []
    metrics_by_year = _annual_metrics(df)
    return [
        FundamentalsHistoryRow(
            code=code, year=y,
            gross_margin=m["gross_margin"], roe_excl=m["roe_excl"],
            report_date=f"{y}-12-31", synced_at=now_iso(),
        )
        for y, m in sorted(metrics_by_year.items(), reverse=True)[:6]  # 多取 1 年兜底
    ]
```

---

## 五、同步入口

`sync_service.py` 新增 `sync_fundamentals_history(db_path, **kwargs)`：

- 走 `sync_hs300_metadata` 已同步的代码集合
- 共用 `sync_service` 的并发/限速/重试机制（与 `sync_fundamentals` 同款）
- 失败单只股票降级日志 + 计入 `fetch_failed.log`，不阻塞其他
- 文档：`logs/fetch_success.log` / `logs/fetch_failed.log`

CLI：`uv run a-finder sync-fundamentals-history --db hs300.db --concurrency 4 --rate 5 --retries 3`

`sync-industry`（同时新增）：

- 调用 `ak.stock_individual_info_em(symbol=code)` 取「行业」字段
- 一次性回填 `hs300_metadata.industry`
- 同款并发/限速/重试

CLI：`uv run a-finder sync-industry --db hs300.db`

---

## 六、策略模块

新文件：`strategies/linyuan_multi_factor.py`

```python
@dataclass
class LinYuanConfig(MultiFactorConfig):
    name: str = "林园 财务质量主导"
    industries: Tuple[str, ...] = ("医药生物", "中药", "食品饮料",
                                    "机械设备", "电力设备", "汽车整车")  # 申万一级映射
    gross_margin_min: float = 0.40
    roe_excl_min: float = 0.15
    continuity_years: int = 5
    top_n: int = 20


class LinYuanMultiFactor(MultiFactorBase):
    def _filter_candidates(self, stocks: List[Stock]) -> List[Stock]:
        # 1) 行业白名单（Stock.sector 来自 hs300_metadata.industry）
        # 2) 连续 N 年 (gross_margin > min ∧ roe_excl > min)，缺年报算断裂
        ...
```

`strategies/__init__.py` 在 `STRATEGIES` 字典内增加 `"林园": linyuan_multi_factor.detect`，但 `MultiFactorBase.select()` 返回 `SelectionResult`，不直接进 `STRATEGIES.detect` 信号流（信号流是单日 buy/sell 检测，多因子走另外路径）。

更准确做法：新增 `strategies/MULTI_FACTOR_STRATEGIES = {"linyuan": LinYuanMultiFactor}`，CLI 单独暴露子命令。

---

## 七、CLI 暴露

`cli_layer.py` 新增子命令：

```bash
uv run a-finder linyuan-picks --top 20
uv run a-finder linyuan-picks --top 20 --dry-run      # 仅打印候选，不写 daily_picks
uv run a-finder sync-fundamentals-history --db hs300.db
uv run a-finder sync-industry --db hs300.db
```

---

## 八、测试覆盖

新增 `tests/test_linyuan_strategy.py`（TDD，pyproject 已配 `tests/`）：

1. `test_annual_metrics_filters_quarterly_reports` — 给定混合年报+季报 DataFrame，断言只取 12 月列
2. `test_fetch_fundamentals_history_returns_per_year_rows` — mock akshare，断言返回 yearly 列表
3. `test_linyuan_filter_requires_5_consecutive_years` — 4 年连续合格 / 缺 1 年都应剔除
4. `test_linyuan_filter_industry_whitelist` — 行业不在白名单的剔除
5. `test_linyuan_strategy_top_n` — N=20 时输出 ≤ 20 只
6. `test_migration_creates_fundamentals_history` — 跑迁移后表存在 + 索引存在
7. `test_sync_fundamentals_history_writes_rows` — mock fetch，断言 upsert 写入

目标：**测试 +3 / -0，全量 ≥ 200 passed**。

---

## 九、README 更新

- 常用命令加 `linyuan-picks` / `sync-fundamentals-history` / `sync-industry`
- 新增 `## 林园策略 / LinYuan` 章节：选股条件表 + 财务阈值说明 + 同步前置依赖

---

## 十、风险 / 开放问题

1. **akshare 列名格式**：首跑前必须本地验证 `_annual_metrics` 的列匹配正则（`^(\d{4})-12-\d{2}`）。如果 akshare 改为 `2024年报` / `2024-12-31 00:00:00` 等格式，正则需调整；保留 fallback 入口。
2. **扣非 ROE 指标名**：`ak.stock_financial_abstract` 是否真有「净资产收益率(扣非)」行待实测。若无，改用「总资产收益率」或「ROE 减去非经常性损益 / 净资产」折算。
3. **行业字符串映射**：用户给的 4 个口语词映射到申万一级时可能错位（"高端制造" 是宽口径，跨机械设备/电力设备/汽车/国防军工）。建议先调研后写死，或多保留 4-6 个 SW 编码做兜底。
4. **首次同步时长**：300 只股票 × 2 报告期接口（基础 + 历史），按 5 req/s 估算 ~120s；可接受。

---

## 十一、评审 / 实施

- 评审通过后，将本文档 commit；接着调 `writing-plans` skill 拆实施计划。
- 实施按 TDD：先测试 → 迁移 → fetch → sync → 策略 → CLI → README。
- 不引入新依赖（akshare / pandas / sqlite 已有）。