# Flask Web 看板设计

日期：2026-08-16
状态：已批准

## 目标

将现有 stdlib `web_server.py` 的选股结果展示，升级为 Flask 后端 + Bootstrap/jQuery（BootCDN）前端的单页看板。看板只读 `daily_picks` 预计算结果，提供两个刷新按钮（重算榜单 / 同步行情并重算）。

## 范围

- 单页看板：按日期展示 `均线` 与 `买入信号` 两个榜单。
- 两个刷新按钮，异步执行 + 前端轮询状态。
- 只读预计算结果（`daily_picks` 表），不做单票信号、不做实时买入信号列表。
- 保留 `web_server.py` 作为纯 stdlib 备用实现，不动其代码。

## 架构

```
Browser (Bootstrap5 + jQuery, BootCDN)
   │  GET / · GET /api/dates · GET /api/picks · POST /api/refresh · GET /api/jobs/<id>
   ▼
app.py (Flask) ──读──> hs300.db (daily_picks 表)
   │  后台线程: pick_history.run_picks(db, top, do_sync)
   └── 内存 job 状态 dict + threading.Lock
```

## 接口

| 路由 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 返回单页 HTML（BootCDN 引 Bootstrap 5 + jQuery） |
| `/api/dates` | GET | 返回降序日期列表 `["2026-08-15", ...]` |
| `/api/picks?date=` | GET | 返回该日 `均线` + `买入信号` 两个榜单 |
| `/api/refresh` | POST | body `{"sync": bool}`；启动后台线程跑 `run_picks`，返回 `{job_id}` |
| `/api/jobs/<id>` | GET | 返回 `{status, message}`；status ∈ pending/running/done/error |

### 榜单数据结构

`/api/picks` 返回：

```json
{
  "date": "2026-08-15",
  "groups": {
    "均线": [{"rank":1,"code":"...","name":"...","strategy":"...","buy":..,"stop":..,"target":..,"score":..}],
    "买入信号": [{"rank":1,...,"score":null}]
  }
}
```

复用现有 `web_server.py` 的 `picks_for_date` 查询语义（`SELECT rank, kind, code, name, strategy, buy, stop, target, score FROM daily_picks WHERE date = ? ORDER BY kind, rank`）。

## 数据流

1. 页面加载 → `GET /api/dates` 填下拉框 → 选中默认最新日期 → `GET /api/picks?date=` 渲染两个表格。
2. 「重算榜单」按钮 → `POST /api/refresh {"sync": false}` → 返回 `job_id` → 前端每 1 秒 `GET /api/jobs/<id>` 轮询 → 状态 `done` 后重拉 `/api/dates` + `/api/picks`。
3. 「同步行情并重算」按钮 → 同上，但 `sync: true`（走 `run_picks` 内部 `sync_hs300` 增量同步，再重算落库）。

## 刷新任务管理

- 后台线程：`threading.Thread(target=run_picks, args=(db_path, top, do_sync))`，daemon=True。
- 内存 `JOBS: dict[str, dict]`，字段 `status`（pending/running/done/error）、`message`。
- `threading.Lock` 保护 JOBS 读写；job 完成/失败后由线程写回状态。
- 同一时刻仅允许一个刷新任务：有新任务且已有 running/pending 任务时，`/api/refresh` 返回 409，body `{"error": "已有任务进行中"}`。
- `run_picks` 的 `top` 来自启动参数 `--top`（默认 10）；`do_sync` 由请求 body 的 `sync` 决定。

## 前端

- 依赖（BootCDN，直接引用 CDN，不本地打包）：
  - Bootstrap 5 CSS/JS：`https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/...`
  - jQuery 3.7.1：`https://cdn.bootcdn.net/ajax/libs/jquery/3.7.1/jquery.min.js`
- 单页：顶部标题 + 日期下拉框 + 两个刷新按钮 + 状态提示条 + 两个榜单表格。
- 轮询逻辑用 jQuery `$.ajax` / `$.getJSON`；禁用重复提交（按钮 pending 时 disabled）。

## 错误处理

- DB 无数据 → `/api/dates` 返回空数组，页面提示「无数据，请先同步行情」。
- `run_picks` 抛异常 → job 置 `error`，`message` 带 `repr(e)` 摘要，前端红色提示条展示。
- `/api/picks` 缺少 `date` 参数 → 默认取最新日期；无任何日期 → 空结果。
- 非法 job id → `/api/jobs/<id>` 返回 404。

## 文件改动

1. `app.py`（新建）：Flask 应用 + 路由 + job 管理 + 内嵌 HTML 模板字符串。
2. `pyproject.toml`：`dependencies` 追加 `flask`。
3. `run_web.sh`：改调 `app.py`（保留 `--db`、`--port`，新增 `--top`）。
4. `web_server.py`：保留不动。

## 测试

- `app_test.py`（新建）：用 Flask `test_client` + 临时 SQLite 断言：
  - `/api/dates` 返回日期列表；
  - `/api/picks?date=` 返回分组榜单；
  - `/api/refresh` 启动 job 并轮询至 done（用 `do_sync=False` 的轻量路径）。
- 非功能：无需额外前端测试，手动验证渲染即可。

## 非目标

- 不做单票信号页、实时买入信号列表、概览页。
- 不引入数据库 ORM、不引入前端构建工具、不引入额外 JS 框架。
- 不改动 `web_server.py` 与既有 CLI 逻辑。
