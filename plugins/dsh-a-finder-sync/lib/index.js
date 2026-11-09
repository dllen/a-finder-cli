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

import { defineTool } from '@deepseek-ai/dsh-tools'
import { readLatestTradeDate, readPublishedDate, resolveProjectDir, runSitePipeline, summarize } from './sync.js'
import { DEFAULT_REMIND_TIME, TASK_TITLE, ensureDailyReminder } from './schedule.js'

/** Tool output schema: a plain string the agent reads as the run summary. */
const output = {
  schema: { type: 'string' },
  render: (_args, value) => [{ type: 'text', text: String(value) }],
}

/** The HTTP prefix the client button talks to. */
const API_PREFIX = '/api/a-finder'

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
    timeoutMs: config.timeoutMs ?? 30 * 60 * 1000,
    remindTime: config.remindTime ?? DEFAULT_REMIND_TIME,
    timeZone: config.timeZone ?? 'Asia/Shanghai',
    autoSchedule: config.autoSchedule !== false,
  }

  // One sync at a time. Two overlapping pipelines would race on the database
  // and on the gh-pages branch, and the second one would publish the older
  // tree over the newer one.
  let running = null

  /**
   * Run the pipeline and report the result.
   *
   * @param {boolean} push - Whether this run should publish.
   * @returns {Promise<object>} A structured run result.
   */
  const performSync = async (push) => {
    if (running !== null) {
      return { ok: false, busy: true, message: '已有同步在进行中，请等待其完成。' }
    }
    const task = (async () => {
      try {
        const projectDir = await resolveProjectDir(settings.projectDir)
        const result = await runSitePipeline({
          projectDir,
          db: settings.db,
          top: settings.top,
          out: settings.out,
          push,
          timeoutMs: settings.timeoutMs,
        })
        const latest = await readLatestTradeDate(projectDir + '/' + settings.db).catch(() => '')
        const published = await readPublishedDate(projectDir + '/' + settings.out + '/data').catch(() => '')
        return { ok: result.ok, result, latest, published, message: summarize(result, latest, published) }
      } catch (error) {
        return {
          ok: false,
          result: { ok: false, code: -1, output: String(error), timedOut: false },
          latest: '',
          published: '',
          message: '同步失败：' + (error instanceof Error ? error.message : String(error)),
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

  ctx.tools.register(defineTool({
    name: 'a_finder_sync',
    description:
      '同步 a-finder 沪深300 行情数据：抓取最新交易日行情、重算选股榜单、导出静态站点并推送到 gh-pages。' +
      '在用户要求「补齐今天数据」「同步行情」「发布站点」时调用。返回本次同步的结果摘要与最新数据日期。',
    parameters: {
      push: {
        type: 'boolean',
        description: '是否推送到 gh-pages；默认 true。只想更新本地数据时传 false。',
      },
    },
    output,
    async execute(args) {
      const push = args?.push === undefined ? settings.push : Boolean(args.push)
      const outcome = await performSync(push)
      return outcome.message
    },
  }))

  // ---- HTTP routes for the Web GUI button ----

  const readJsonBody = async (request) => {
    try {
      const raw = await request.text()
      return raw ? JSON.parse(raw) : {}
    } catch {
      return {}
    }
  }

  const registerRoutes = (webCtx) => {
    webCtx.effect(() =>
      webCtx.webServer.register({
        kind: 'prefix',
        path: API_PREFIX,
        handler: async (req, res) => {
          const url = new URL(req.url ?? '/', 'http://localhost')
          const path = url.pathname.slice(API_PREFIX.length) || '/'
          const send = (status, payload) => {
            const body = JSON.stringify(payload)
            res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
            res.end(body)
          }

          if (req.method !== 'POST' && req.method !== 'GET') {
            send(405, { ok: false, message: 'Method not allowed' })
            return
          }

          try {
            if (path === '/status') {
              const projectDir = await resolveProjectDir(settings.projectDir)
              const latest = await readLatestTradeDate(projectDir + '/' + settings.db).catch(() => '')
              const published = await readPublishedDate(projectDir + '/' + settings.out + '/data').catch(() => '')
              send(200, { ok: true, running: running !== null, latest, published })
              return
            }

            if (path === '/sync') {
              const body = await readJsonBody(req)
              const push = body.push === undefined ? settings.push : Boolean(body.push)
              const outcome = await performSync(push)
              send(outcome.ok ? 200 : 500, {
                ok: outcome.ok,
                busy: outcome.busy === true,
                message: outcome.message,
                latest: outcome.latest ?? '',
                published: outcome.published ?? '',
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
    const own = service
    schedCtx.effect(() => () => { if (service === own) service = null }, 'a-finder-sync: schedule service')

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
