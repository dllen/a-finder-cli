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
- ma-picks：基于均线条件输出选股结果
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
```

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

## 一键管理

```bash
bash manage.sh status
bash manage.sh start overview
bash manage.sh stop
bash manage.sh restart picks --top 5
```
