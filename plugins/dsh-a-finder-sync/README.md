# dsh-a-finder-sync

a-finder 沪深300 行情同步的 DSH 插件：

- **每天 17:00 提醒** —— 用 DSH 内置的 Schedule 服务建一个持久化定时任务，到点向会话投递一条「请同步」的提示；
- **一键同步** —— Web GUI 输入区上方多一个「同步数据」按钮，点一下就跑完整流水线并推送 gh-pages。

两件事都不依赖本机的 cron / launchd：任务存在 `DSH_HOME` 里，跟着 GUI 走，重启不丢。

## 它做什么

插件不重写同步逻辑，而是调用仓库自己的 `a-finder.sh site ... --push`：

    行情同步 -> 重算选股 -> 导出静态站点 -> 单调性校验 -> 推送 gh-pages

这条流水线已经带了对齐保护（不比线上旧）、复权口径检查和单写者约束。在这里重写一份等于给站点制造第二个真相源。

## 安装

```bash
# 1. 安装依赖（DSH 会把包登记进 profile 的 package.json）
dsh plugin --profile desktop add file:/绝对路径/plugins/dsh-a-finder-sync

# 2. 把插件挂进 profile 的 bundle 列表
#    编辑 ~/.dsh/profiles/desktop/package.json 的 dsh.profile.bundles，
#    在数组末尾加上 "dsh-a-finder-sync"

# 3. 重启 DeepSeek Harness
```

装完可以在 GUI 的「设置 → 插件」里看到它；输入区下方会出现「同步数据」按钮，
「自动化任务」页里会出现名为「a-finder 行情同步提醒」的每日任务。

## 配置

配置写在插件包的 `cordis.patch.yml` 里：

```yaml
- insert:
    - id: a-finder-sync
      name: dsh-a-finder-sync
      config:
        projectDir: /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
        db: hs300.db
        top: 20
        out: site
        push: true
        remindTime: '17:00'
        timeZone: Asia/Shanghai
        autoSchedule: true
```

| 字段 | 含义 |
|---|---|
| `projectDir` | a-finder-cli 仓库根目录，必须含 `a-finder.sh` 和 `hs300.db` |
| `db` / `out` | 库文件名 / 导出目录名（相对 `projectDir`） |
| `top` | 每个榜单取多少条 |
| `push` | 默认是否推送到 gh-pages |
| `remindTime` | 提醒时刻（本地时间） |
| `timeZone` | 提醒时刻所用时区 |
| `autoSchedule` | 设为 false 就不自动建提醒任务，只留工具和按钮 |

也可以用环境变量 `A_FINDER_DIR` 指向仓库目录，省去改配置。

## 三条使用路径

**按钮**：输入区上方的「同步数据」→ 直接 POST `/api/a-finder/sync`，不经过模型回合，所以快、也不受会话状态影响。

**工具**：在会话里说「补齐今天数据」，agent 会调 `a_finder_sync` 工具（可选参数 `push`）。

**定时**：每天 17:00，Schedule 服务把提醒投递给一个会话，agent 收到后自己决定要不要同步。
周末和节假日会提示「今日无需同步」——库里最新的交易日就是判据，不看系统日期。

## 设计要点

**提醒用 `daily` 选择器，不用 RRULE。**
`daily` 收「本地时刻 + IANA 时区」，每天按那个时区重新算下一次发生时刻；
换成 RRULE 配 `TZID` 的话，DSH 的解析器只认瞬时不认墙钟时间（实测 `DTSTART;TZID=Asia/Shanghai:20261109T170000`
被当成 17:00 **UTC** = 次日 01:00 北京），夏令时切换后会漂一小时。

**提醒任务幂等创建。**
插件每次激活都按标题找自己的任务：找到了就原样留着（用户可能改过提示词、暂停过、或故意删了），
找不到才新建。所以用户删掉提醒后不会在下次重启时又被悄悄建回来。

**同一时刻只跑一个同步。**
两个流水线并发会同时写库、同时推 gh-pages，后推的可能把先推的旧树盖掉。
插件用���个 in-flight 标记挡住重入，重入时直接返回「已有同步在进行中」。

**读库用 `sqlite3` 命令行，不用 `node:sqlite`。**
`node:sqlite` 在 Node 24 里还要额外 flag，接口也不稳；`sqlite3` CLI 是仓库本来就有的依赖。

## 目录结构

    plugins/dsh-a-finder-sync/
    ├── package.json        # host + client 双面声明，dsh.bundle 指向 patch
    ├── cordis.patch.yml    # 挂载 host 插件并给配置
    └── lib/
        ├── index.js         # host 半侧：工具 + HTTP 路由 + 定时任务
        ├── sync.js          # 跑流水线、读日期、生成摘要
        ├── schedule.js      # 提醒任务的幂等创建
        └── client.js        # browser 半侧：一键同步按钮

## 排查

```bash
# 看插件有没有挂上、配置对不对
dsh --profile desktop --dump-config | grep -A8 a-finder

# 看定时任务
dsh headless --profile desktop "列出所有定时任务"

# 看同步日志
tail -40 logs/site-run-*.log
```

按钮点了没反应：确认 GUI 里插件已启用（设置 → 插件），且 `projectDir` 下的 `a-finder.sh` 存在。
同步失败时按钮旁会显示退出码和最后几行输出。
