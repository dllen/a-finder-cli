// Host half of the a-finder sync plugin.
//
// Three responsibilities, all of them host-side because only the host can run
// the project's shell pipeline and only the host owns the durable Schedule:
//
//   1. a model tool (a_finder_sync) so the agent can sync on request;
//   2. an HTTP route (/api/a-finder/*) so the Web GUI button can trigger the
//      same code path without going through a model turn;
//   3. a daily 17:00 Schedule task that reminds the agent to sync.
//
// Routes 1 and 2 share one implementation, so the button and the agent cannot
// drift apart: the button does exactly what the tool does.
//
// ⚠️ 这个文件**不 import 任何 @deepseek-ai/* 宿主包**。
//
// 教训（2026-11-09 实测）：插件装进 profile 后，宿主包并不在 profile 的
// node_modules 里（pnpm 的 peer 不会自动链接）。原先 `import { defineTool }
// from '@deepseek-ai/dsh-tools'` 直接 ERR_MODULE_NOT_FOUND ⇒ **整个 host 半侧
// 加载失败** ⇒ 工具和 /api 路由都不存在。而 browser bundle 是 client-modules
// 独立提供的，照常加载 ⇒ 按钮还在、点下去落到 DSH 自己的 /api 守卫上返回
// 401 ⇒ 界面只显示「同步失败」，看起来像同步跑失败了，实际连路由都没到。
//
// ctx.tools.register 只要求 { name, output: { schema, render } }，defineTool
// 只是把参数规格转成 JSON Schema 的语法糖。这里直接写 JSON Schema，零依赖。

import { appendFile, mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import {
  readCbLatestDate,
  readLatestTradeDate,
  readPublishedDate,
  resolveProjectDir,
  runSitePipeline,
  summarize,
} from './sync.js'
import { DEFAULT_REMIND_TIME, TASK_TITLE, ensureDailyReminder } from './schedule.js'

/** The HTTP prefix the client button talks to. */
const API_PREFIX = '/api/a-finder'

/**
 * Tool output: one string the agent reads as the run summary.
 * `register` validates `schema` and calls `render` to project the value.
 */
const TOOL_OUTPUT = {
  schema: { type: 'string' },
  render: (_args, value) => [{ type: 'text', text: String(value) }],
}

/**
 * Host plugin entry.
 *
 * @param {object} ctx - The Cordis context.
 * @param {object} config - Plugin configuration.
 * @returns {void}
 */
export function apply(ctx, config = {}) {
  const settings = {
    projectDir: config.projectDir ?? process.env.A_FINDER_DIR ?? '',
    db: config.db ?? 'hs300.db',
    top: config.top ?? 20,
    out: config.out ?? 'site',
    push: config.push !== false,
    // The pipeline syncs the bond board by default; `cb: false` only opts out
    // (the plugin passes --no-cb, it never runs the bond step itself).
    cb: config.cb !== false,
    timeoutMs: config.timeoutMs ?? 30 * 60 * 1000,
    remindTime: config.remindTime ?? DEFAULT_REMIND_TIME,
    timeZone: config.timeZone ?? 'Asia/Shanghai',
    autoSchedule: config.autoSchedule !== false,
  }

  // One sync at a time. Two overlapping pipelines would race on the database
  // and on the gh-pages branch, and the second one would publish the older
  // tree over the newer one. (Observed for real: two concurrent runs made
  // pick_history.py die on a locked database.)
  let running = null

  /**
   * Append one line to the plugin's own log, so a failed click leaves evidence.
   * The pipeline's output is far too long for the HTTP response, and the host
   * console is not something the user reads.
   *
   * @param {string} projectDir - Resolved project root.
   * @param {string} line - Text to append.
   * @returns {Promise<void>} Resolves even when logging itself fails.
   */
  const log = async (projectDir, line) => {
    try {
      const dir = join(projectDir, 'logs')
      await mkdir(dir, { recursive: true })
      await appendFile(join(dir, 'a-finder-sync.log'), line + '\n', 'utf8')
    } catch {
      // Logging must never be the thing that breaks a sync.
    }
  }

  /**
   * Run the pipeline and report the result.
   *
   * @param {boolean} push - Whether this run should publish.
   * @param {boolean} cb - Whether this run should sync the bond board.
   * @returns {Promise<object>} A structured run result.
   */
  const performSync = async (push, cb) => {
    if (running !== null) {
      return { ok: false, busy: true, message: '已有同步在进行中，请等待其完成。' }
    }
    const task = (async () => {
      let projectDir = ''
      try {
        projectDir = await resolveProjectDir(settings.projectDir)
        const startedAt = new Date().toISOString()
        await log(projectDir, '[' + startedAt + '] sync start push=' + push + ' cb=' + cb)
        const result = await runSitePipeline({
          projectDir,
          db: settings.db,
          top: settings.top,
          out: settings.out,
          push,
          cb,
          timeoutMs: settings.timeoutMs,
        })
        const latest = await readLatestTradeDate(join(projectDir, settings.db)).catch(() => '')
        const published = await readPublishedDate(join(projectDir, settings.out, 'data')).catch(() => '')
        // A database created before the bond tables existed has no cb_picks;
        // that is a legitimate state, so an unreadable date degrades to ''.
        const cbLatest = await readCbLatestDate(join(projectDir, settings.db)).catch(() => '')
        const message = summarize(result, latest, published, cbLatest)
        await log(projectDir, '[' + new Date().toISOString() + '] sync end ok=' + result.ok + ' code=' + result.code)
        // Keep the tail of a failed run next to the outcome: the button only
        // shows the summary, so without this the reason is lost.
        if (!result.ok) {
          await log(projectDir, result.output.split('\n').slice(-40).join('\n'))
        }
        return { ok: result.ok, result, latest, published, cbLatest, message }
      } catch (error) {
        const message = '同步失败：' + (error instanceof Error ? error.message : String(error))
        if (projectDir !== '') await log(projectDir, '[' + new Date().toISOString() + '] ' + message)
        return {
          ok: false,
          result: { ok: false, code: -1, output: String(error), timedOut: false },
          latest: '',
          published: '',
          cbLatest: '',
          message,
        }
      }
    })()
    running = task
    try {
      return await task
    } finally {
      running = null
    }
  }

  ctx.tools.register({
    name: 'a_finder_sync',
    description:
      '同步 a-finder 沪深300 行情数据：抓取最新交易日行情、重算选股榜单、同步可转债榜单、导出静态站点并推送到 gh-pages。' +
      '在用户要求「补齐今天数据」「同步行情」「发布站点」时调用。返回本次同步的结果摘要与最新数据日期。',
    parameters: {
      type: 'object',
      properties: {
        push: {
          type: 'boolean',
          description: '是否推送到 gh-pages；默认 true。只想更新本地数据时传 false。',
        },
        cb: {
          type: 'boolean',
          description: '是否同步可转债榜单；默认 true。只想更新股票行情与选股时传 false。',
        },
      },
    },
    output: TOOL_OUTPUT,
    async execute(args) {
      const push = args?.push === undefined ? settings.push : Boolean(args.push)
      const cb = args?.cb === undefined ? settings.cb : Boolean(args.cb)
      const outcome = await performSync(push, cb)
      return outcome.message
    },
  })

  // ---- HTTP routes for the Web GUI button ----

  /**
   * Read a JSON body off a Node IncomingMessage.
   *
   * The web server hands route handlers a node:http request, which is a
   * readable stream. It has no Fetch `.text()` — calling that throws, and a
   * silent catch there would quietly drop the caller's `push` flag.
   *
   * @param {object} req - The node:http request.
   * @returns {Promise<object>} The parsed body, or an empty object.
   */
  const readJsonBody = (req) =>
    new Promise((resolve) => {
      let raw = ''
      req.on('data', (chunk) => {
        raw += chunk
        if (raw.length > 1_000_000) raw = raw.slice(0, 1_000_000)
      })
      req.on('end', () => {
        try {
          resolve(raw ? JSON.parse(raw) : {})
        } catch {
          resolve({})
        }
      })
      req.on('error', () => resolve({}))
    })

  const registerRoutes = (webCtx) => {
    webCtx.effect(() =>
      webCtx.webServer.register({
        kind: 'prefix',
        path: API_PREFIX,
        handler: async (req, res) => {
          const url = new URL(req.url ?? '/', 'http://localhost')
          const path = url.pathname.slice(API_PREFIX.length) || '/'
          const send = (status, payload) => {
            res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
            res.end(JSON.stringify(payload))
          }

          if (req.method !== 'POST' && req.method !== 'GET') {
            send(405, { ok: false, message: 'Method not allowed' })
            return
          }

          try {
            if (path === '/status') {
              const projectDir = await resolveProjectDir(settings.projectDir)
              const latest = await readLatestTradeDate(join(projectDir, settings.db)).catch(() => '')
              const published = await readPublishedDate(join(projectDir, settings.out, 'data')).catch(() => '')
              const cb = await readCbLatestDate(join(projectDir, settings.db)).catch(() => '')
              send(200, { ok: true, running: running !== null, latest, published, cb })
              return
            }

            if (path === '/sync') {
              const body = await readJsonBody(req)
              const push = body.push === undefined ? settings.push : Boolean(body.push)
              const cb = body.cb === undefined ? settings.cb : Boolean(body.cb)
              const outcome = await performSync(push, cb)
              send(outcome.ok ? 200 : 500, {
                ok: outcome.ok,
                busy: outcome.busy === true,
                message: outcome.message,
                latest: outcome.latest ?? '',
                published: outcome.published ?? '',
                cb: outcome.cbLatest ?? '',
              })
              return
            }

            send(404, { ok: false, message: 'Unknown endpoint' })
          } catch (error) {
            send(500, { ok: false, message: error instanceof Error ? error.message : String(error) })
          }
        },
      }),
      'a-finder-sync: api routes',
    )
  }
  if (typeof ctx.inject === 'function') ctx.inject(['webServer'], registerRoutes)

  // ---- The daily reminder ----

  const registerSchedule = (schedCtx) => {
    const service = schedCtx.schedule
    if (!settings.autoSchedule) return

    void ensureDailyReminder({
      service,
      projectDir: settings.projectDir,
      time: settings.remindTime,
      timeZone: settings.timeZone,
      target: 'new_once',
    })
      .then((outcome) => {
        schedCtx.logger?.info?.(
          '[a-finder-sync] 每日 ' + settings.remindTime + ' 提醒任务：' + (outcome.detail ?? outcome.action),
        )
      })
      .catch((error) => {
        // A missing schedule service or a rejected task must not break the
        // plugin: the tool and the button still work without the reminder.
        schedCtx.logger?.warn?.('[a-finder-sync] 提醒任务创建失败：' + String(error))
      })
  }
  if (typeof ctx.inject === 'function') ctx.inject(['schedule'], registerSchedule)
}

export { TASK_TITLE, DEFAULT_REMIND_TIME }
export const name = 'dsh-a-finder-sync'
export const inject = ['tools']
