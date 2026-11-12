# dsh-a-finder-sync

a-finder 沪深300 行情同步的 DSH 插件：

- **每天 17:00 提醒** —— 用 DSH 内置的 Schedule 服务建一个持久化定时任务，到点向会话投递一条「请同步」的提示；
- **一键同步** —— Web GUI 输入区上方多一个「同步数据」按钮，点一下就跑完整流水线并推送 gh-pages。

两件事都不依赖本机的 cron / launchd：任务存在 `DSH_HOME` 里，跟着 GUI 走，重启不丢。

## 它做什么

插件不重写同步逻辑，而是调用仓库自己的 `a-finder.sh site ... --push`：

    行情同步 -> 重算选股 -> 可转债榜单 -> 导出静态站点 -> 单调性校验 -> 推送 gh-pages

这条流水线已经带了对齐保护（不比线上旧）、复权口径检查和单写者约束。在这里重写一份等于给站点制造第二个真相源。

**可转债默认包含在同步里**：插件只在 `cb: false`（配置或工具参数）时给命令追加 `--no-cb`，
其余一律交给项目自己的流水线 —— 转债怎么同步、什么时候跳过，由 `a-finder.sh site` 决定。

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
        cb: true
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
| `cb` | 默认是否同步可转债榜单；false 时给流水线追加 `--no-cb` 跳过转债步骤 |
| `remindTime` | 提醒时刻（本地时间） |
| `timeZone` | 提醒时刻所用时区 |
| `autoSchedule` | 设为 false 就不自动建提醒任务，只留工具和按钮 |

也可以用环境变量 `A_FINDER_DIR` 指向仓库目录，省去改配置。

## 三条使用路径

**按钮**：输入区上方的「同步数据」→ 直接 POST `/api/a-finder/sync`（请求体 `{push: true, cb: true}`），
不经过模型回合，所以快、也不受会话状态影响。按钮旁的状态行显示「库 X · 转债 Y · 站点 Z」，
转债还没有数据时显示 —。

**工具**：在会话里说「补齐今天数据」，agent 会调 `a_finder_sync` 工具（可选参数 `push` 与 `cb`，都默认 true）。
只想更新股票行情与选股时传 `cb: false`，插件就会走 `--no-cb`。

**定时**：每天 17:00，Schedule 服务把提醒投递给一个会话，agent 收到后自己决定要不要同步。
提醒词里写明同步包含「行情 + 选股 + 可转债榜单」。
周末和节假日会提示「今日无需同步」——库里最新的交易日就是判据，不看系统日期。

## ⚠️ 转债数据源的特殊性

转债榜单和股票行情不是一回事，改这块前先读完这段：

- **数据源只有实时快照**（akshare `bond_zh_cov` 没有历史日接口）⇒ 一个交易日只可能有
  「当天」这一份转债榜单，补历史是补不出来的，只能缺着。
- **非交易日跳过**：流水线给 `cb-picks` 传 `--date <库里最新交易日>`，而转债管线只接受
  「等于今天」的日期 ⇒ 周末/节假日自动拒绝，CLI 正常退出、流水线继续导出与发布。
- **同步失败时 `--date` 守卫拒绝错标**：行情没同步到位（库最新日落后于今天）时，当天实时快照
  会被拒绝写进旧日期。**错标比缺失更糟** —— 把今天的快照挂在昨天的日期上，页面会显示一份
  看似正常、实则是未来数据的榜单，且再也无法从库里分辨出来。
- 所以「转债日期 = 库里最新交易日」是正常状态，不是 bug；插件摘要与按钮里的转债日期为空/—
  只表示还没有转债榜单，不代表行情没同步。

**不要**为了让转债在周末也更新而删掉 `--date`，也不要在插件里另写一份转债同步逻辑 ——
要改就改 `a-finder.sh site`，这是唯一真相源。

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

## ⚠️ 四个实测踩过的坑

### 1. 插件不许 import 宿主包

宿主包（`@deepseek-ai/*`）**不在 profile 的 node_modules 里** —— pnpm 的 peer 不会
自动链接。第一版写了 `import { defineTool } from '@deepseek-ai/dsh-tools'`，
结果宿主加载插件时直接 `ERR_MODULE_NOT_FOUND` ⇒ **整个 host 半侧死掉** ⇒
工具和 `/api` 路由都不存在。

而 browser bundle 由 client-modules 独立提供，照常加载 ⇒ **按钮还在**，点下去
请求落到 DSH 自己的 `/api` 守卫上返回 **401** ⇒ 界面只显示「同步失败」，
看起来像同步跑失败了，实际连路由都没到。

所以 `lib/index.js` 只用 `node:*` 内置模块。`ctx.tools.register` 只要求
`{ name, output: { schema, render } }`，`defineTool` 只是把参数规格转成
JSON Schema 的语法糖 —— 直接写 JSON Schema 即可，零依赖。

### 2. `dsh plugin add` 不会刷新 `file:` 依赖的副本

pnpm 对 `file:` 依赖做**拷贝**而非软链，且**改了版本号也不重拷**（实测
`add` 反复报 `resolved 123, reused 1, added 0`，profile 里的文件时间戳一直
停在第一次安装的时刻）。后果是：**你改了仓库代码，宿主跑的还是旧版**，
而且看不出任何异常。

必须 `remove` 再 `add`：

```bash
dsh plugin --profile desktop remove dsh-a-finder-sync
dsh plugin --profile desktop add file:/绝对路径/plugins/dsh-a-finder-sync
```

改完代码一定核对 profile 里的副本：

```bash
P=~/.dsh/profiles/desktop/node_modules/dsh-a-finder-sync
grep '"version"' $P/package.json
grep -n '^import' $P/lib/index.js | grep '@deepseek-ai' || echo "(无宿主依赖 ✓)"
ls -la $P/lib/index.js   # 看时间戳
```

### 3. 工具 schema 里 `required` 只能为 `true`

`defineTool` 会以 `JsonSchemaError: parameters.push.required must be true when
present` 拒绝 `required: false`。可选参数**直接省略这个键**。

### 4. 路由拿到的是 node:http 请求，不是 Fetch Request

`webServer.register({ handler })` 的 `req` 是 `IncomingMessage`（可读流），
**没有 `.text()`**。用 `.text()` 会抛错，被 catch 吞掉后 body 变空 ——
`push` 之类的参数就被静默忽略了。要按流读：

```js
const readJsonBody = (req) => new Promise((resolve) => {
  let raw = ''
  req.on('data', (c) => { raw += c })
  req.on('end', () => { try { resolve(raw ? JSON.parse(raw) : {}) } catch { resolve({}) } })
  req.on('error', () => resolve({}))
})
```

## 排查

```bash
# 看插件有没有挂上、配置对不对
dsh --profile desktop --dump-config | grep -A8 a-finder

# 看定时任务
dsh headless --profile desktop "列出所有定时任务"

# 看同步日志
tail -40 logs/site-run-*.log
```

按钮点了没反应 / 显示同步失败：先看插件自己的日志（每次点击都会写一行起止，
失败时还会附上流水线最后 40 行）：

```bash
tail -60 logs/a-finder-sync.log
```

日志里**没有这次点击的记录** ⇒ 请求根本没到插件（多半是没重启 GUI，或插件没加载成功，
见上面「部署坑 1」）；有记录但 `ok=false` ⇒ 流水线本身失败，看附带的输出尾部。
