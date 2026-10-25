# 数据源限流智能规避 — SourceHealthTracker 设计

**日期**：2026-10-25
**状态**：设计中（待批准后实施）

---

## 一、目标

解决数据同步时的源限流问题：

1. **智能限流检测**——同步开始前自动探测各数据源可用性，跳过被封源
2. **多数据源冗余**——东财/新浪/腾讯三源动态切换，不依赖单一源
3. **同步提速**——避免 300 只股票逐一撞已封源的墙

**非目标**：
- ❌ 不新增数据源（现有东财/新浪/腾讯已足够）
- ❌ 不改数据库 schema
- ❌ 不改 CLI 接口（用户无感知）
- ❌ 不做跨会话持久化（每次同步重新探针）

---

## 二、现状分析

### 当前 fallback 链

```
fetch_daily_kline_with_name()
  ├── 东财 push2his (主源)
  │     └── 失败 → _fetch_daily_kline_fallback()
  │             ├── 新浪
  │             └── 腾讯
  └── 返回 (rows, name)
```

### 问题

| 问题 | 影响 |
|------|------|
| 东财被封时，300 只股票每只都先撞一次东财 | 浪费 ~300 次请求，加重限流风险，拖慢同步 |
| 无健康状态跟踪 | 不知道当前哪个源可用，无法预防性跳过 |
| akshare `stock_zh_a_hist` 走东财 | 东财被封时此路完全不可用 |

### 各源当前状态（2026-10-25 实测）

| 源 | 状态 | 接口 |
|---|---|---|
| 东财 `push2his` | ❌ 被封（`RemoteDisconnected`） | `fetch_daily_kline_with_name` |
| 腾讯 `fqkline` | ✅ 正常 | `fetch_daily_kline_tencent` |
| 新浪 | ✅ 正常 | `fetch_daily_kline_sina` / `ak.stock_zh_a_daily` |
| akshare `stock_zh_a_hist` | ❌ 走东财被封 | — |

---

## 三、设计方案

### 架构

```
sync_hs300_range()
  │
  ▼
SourceHealthTracker.probe()          ← 新增：探针测试，~2-3秒
  │     用 600519 依次测试东财→新浪→腾讯
  │     记录可用性，动态排序
  │
  ▼
ThreadPoolExecutor (300 只股票并发)
  │
  ▼
_fetch_with_retry(code, ..., tracker)  ← 修改：传入 tracker
  │
  ▼
fetch_daily_kline_smart(code, ..., tracker)  ← 新增
  │     按 tracker 优先级尝试各源
  │     成功 → record_success()
  │     失败 → record_failure()
  │
  ▼
返回 (rows, name)
```

### 核心组件

#### `SourceHealthTracker`（~60 行，data_providers.py）

```python
class SourceHealthTracker:
    """跟踪各数据源的可用性，动态调整优先级。"""

    SOURCES = ["eastmoney", "sina", "tencent"]
    PROBE_CODES = ["600519"]           # 探针股票：贵州茅台
    MAX_CONSECUTIVE_FAILURES = 3       # 连续失败 3 次后降级

    def __init__(self):
        self._health = {name: {"available": True, "fail_count": 0}
                        for name in self.SOURCES}
        self._ordered_sources = list(self.SOURCES)
        self._lock = threading.Lock()  # 线程安全

    def probe(self) -> None:
        """同步开始前，用探针股票测试各源。"""

    def get_ordered_sources(self) -> List[str]:
        """返回按当前优先级排序的源列表。"""

    def record_success(self, source: str) -> None:
        """记录成功，重置 fail_count。"""

    def record_failure(self, source: str) -> None:
        """记录失败，超过阈值降级到列表末尾。"""
```

#### `fetch_daily_kline_smart()`（~30 行，data_providers.py）

```python
def fetch_daily_kline_smart(
    code: str, start_date: str, end_date: str,
    tracker: SourceHealthTracker,
) -> tuple[List[PriceRow], str]:
    """按 tracker 优先级依次尝试各数据源。"""
    for source in tracker.get_ordered_sources():
        if not tracker.is_available(source):
            continue
        try:
            rows, name = _try_source(source, code, start_date, end_date)
            if rows:
                tracker.record_success(source)
                return rows, name
            tracker.record_failure(source)
        except Exception:
            tracker.record_failure(source)
    return [], ""
```

#### `_fetch_with_retry()` 改造（sync_service.py）

只改一行：把 `fetch_daily_kline_with_name` 换成 `fetch_daily_kline_smart`，并传入 `tracker`。

#### `sync_hs300_range()` 改造（sync_service.py）

在创建 ThreadPoolExecutor 之前加：

```python
tracker = SourceHealthTracker()
tracker.probe()
# 日志: "数据源优先级: sina > tencent > eastmoney"
```

### 线程安全

`SourceHealthTracker` 被多个工作线程共享，用 `threading.Lock()` 保护：
- `record_success()` — 加锁
- `record_failure()` — 加锁（含降级时的列表重排）
- `get_ordered_sources()` — 加锁（返回副本）

### 降级策略

| 触发条件 | 行为 | 恢复条件 |
|---------|------|---------|
| 探针阶段某源失败 | 移到优先级列表末尾 | 下次同步重新探针 |
| 同步中某源连续失败 3 次 | 移到列表末尾，`available=False` | 本次同步内不再尝试 |
| 降级后的源 | `fetch_daily_kline_smart` 直接跳过 | 下次同步 probe 时重新评估 |

---

## 四、文件变更

| 文件 | 变更 | 说明 |
|------|------|------|
| `data_providers.py` | **新增** `SourceHealthTracker` 类 | ~60 行 |
| `data_providers.py` | **新增** `fetch_daily_kline_smart()` | ~30 行 |
| `sync_service.py` | **修改** `_fetch_with_retry()` | +1 参数，换 1 行调用 |
| `sync_service.py` | **修改** `sync_hs300_range()` | +3 行（创建 tracker + probe + 日志） |
| `tests/test_source_health.py` | **新增** | ~150 行测试 |

**零改动**：`db_schema.py`、`config.py`、CLI 接口、数据库。

---

## 五、测试策略

### 单元测试（`tests/test_source_health.py`）

| 测试 | 验证点 |
|------|--------|
| `test_probe_marks_eastmoney_unavailable_when_blocked` | 东财被封时 probe 正确标记 |
| `test_probe_all_available_default_order` | 全通时保持默认优先级 |
| `test_record_failure_demotes_source` | 连续 3 次失败后降级 |
| `test_record_success_resets_fail_count` | 成功后计数归零 |
| `test_thread_safety` | 多线程并发不崩溃 |
| `test_skips_unavailable_source` | smart 函数跳过不可用源 |
| `test_falls_through_on_failure` | 首源失败自动尝试下一源 |
| `test_all_sources_fail_returns_empty` | 全源失败返回空 |

### 集成验证

```bash
# 东财当前被封，验证自动降级
python3 stock_cli.py sync-hs300-range --start 2026-10-01 --end 2026-10-24 \
  --db hs300.db --concurrency 4 --rate 6

# 预期日志:
# [INFO] 数据源优先级: sina > tencent > eastmoney
# [INFO] 区间同步开始: symbols=300 ...
# [INFO] 区间同步完成: rows=3000  (而非 0)
```

---

## 六、风险控制

| 风险 | 缓解 |
|------|------|
| 探针股票 600519 停牌或退市 | 探针失败≠源不可用，需区分"股票无数据"和"连接被拒"。用多只探针 + 异常类型判断 |
| 探针阶段误伤正常源 | 探针用短日期区间（10天），减少请求量；探针失败不等同于源完全不可用 |
| 降级后源恢复但本次同步不再尝试 | 可接受——下次同步 probe 会重新评估 |
| ThreadPoolExecutor 中共享 tracker 的竞态 | `threading.Lock()` 保护所有读写 |
| 向后兼容 | 现有 `fetch_daily_kline_with_name` 等函数保留不动，新函数是增量添加 |

---

## 七、实施顺序

1. 写 `tests/test_source_health.py`（TDD，先写测试）
2. 实现 `SourceHealthTracker` 类
3. 实现 `fetch_daily_kline_smart()`
4. 修改 `sync_service.py` 集成
5. 全量测试
6. 真实环境验证（当前东财正好被封，天然测试场景）

---

## 八、评审 / 实施

- 本文档已获用户批准（4 节设计逐节确认）
- 接着调 `writing-plans` skill 拆实施计划
- 实施按 TDD：先测试 → 实现 → 集成 → 验证
