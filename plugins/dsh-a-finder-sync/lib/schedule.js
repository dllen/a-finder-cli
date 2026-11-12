// The daily 17:00 reminder, kept as a host Schedule task.
//
// Why the host Schedule service instead of cron/launchd: a cron entry or a
// launchd plist lives on one machine. The host Schedule lives in DSH_HOME and
// survives restarts, so the reminder keeps working on whatever machine runs
// the GUI. It also shows up in the GUI's own task list, so the user can see,
// pause or delete it.
//
// The task is created idempotently: on every plugin activation we look for our
// own task by title and only create it when it is missing. That keeps a user who
// deleted the reminder from getting it silently recreated on the next restart.
//
// Timing uses the Schedule service's `daily` selector rather than an RRULE:
// `daily` takes a local wall-clock time plus an IANA zone and recomputes the
// occurrence in that zone every day, so the reminder stays at 17:00 across a
// daylight-saving change. An RRULE with a TZID would pin the *instant* instead
// and drift by an hour when the offset changes.

/** The title of the task this plugin owns. Used to find it again. */
export const TASK_TITLE = 'a-finder 行情同步提醒'

/** Default time of day for the reminder, in the configured time zone. */
export const DEFAULT_REMIND_TIME = '17:00'

/**
 * Build the daily timing selector for the reminder.
 *
 * @param {string} time - Local time of day as HH:MM.
 * @param {string} timeZone - IANA time zone name.
 * @returns {object} A Schedule `daily` selector.
 */
export function buildDailyTiming(time, timeZone) {
  // The Schedule service wants a full local clock time (HH:mm:ss), not HH:mm.
  const normalized = /^\d{1,2}:\d{2}$/.test(time) ? time + ':00' : time
  return { kind: 'daily', daily: { time: normalized, time_zone: timeZone } }
}

/**
 * The prompt delivered to the session when the reminder fires.
 *
 * The reminder is a prompt, not a command: the agent receiving it decides
 * whether to sync right away. Asking for a sync explicitly keeps the agent in
 * charge of the database's state and lets it report what it found.
 *
 * @param {string} projectDir - The project root, so the agent knows where to work.
 * @returns {string} The reminder prompt.
 */
export function buildReminderPrompt(projectDir) {
  return [
    'a-finder 每日行情同步提醒（工作日 17:00）。',
    '',
    '项目目录：' + projectDir,
    '',
    '请执行一次 a-finder 数据同步：调用 a_finder_sync 工具完成「同步行情 + 重算选股 + 可转债榜单 + 导出站点 + 推送 gh-pages」。',
    '转债数据源只有实时快照：非交易日必须跳过转债（工具默认行为即可），不要为了刷新转债而改日期。',
    '如果今天不是 A 股交易日，或库里已经有今天的完整数据，请直接说明「今日无需同步」并给出当前数据状态，不要重复推送。',
  ].join('\n')
}

/**
 * Find this plugin's task among the host's scheduled tasks.
 *
 * @param {object} service - The host schedule service.
 * @returns {Promise<object|undefined>} The task record, if it exists.
 */
export async function findOwnTask(service) {
  const page = await service.list({ limit: 500 })
  const tasks = page?.items ?? []
  return tasks.find((task) => task.title === TASK_TITLE)
}

/**
 * Make sure the daily reminder exists.
 *
 * An existing task is left exactly as it is: the user may have edited its
 * prompt, paused it, or deleted it on purpose, and silently rewriting it would
 * undo their choice.
 *
 * @param {object} options - Options.
 * @param {object} options.service - The host schedule service.
 * @param {string} options.projectDir - The project root.
 * @param {string} options.time - Local time of day as HH:MM.
 * @param {string} options.timeZone - IANA time zone name.
 * @param {string} options.target - Schedule target kind.
 * @returns {Promise<{action: string, id?: string, detail?: string}>} What happened.
 */
export async function ensureDailyReminder(options) {
  const { service, projectDir, time, timeZone, target = 'new_once' } = options
  const existing = await findOwnTask(service)

  if (existing !== undefined) {
    return {
      action: 'kept',
      id: existing.id,
      detail: existing.status === 'active' ? '已存在且启用' : '已存在但已暂停',
    }
  }

  const created = await service.create({
    title: TASK_TITLE,
    prompt: buildReminderPrompt(projectDir),
    timing: buildDailyTiming(time, timeZone),
    target: { kind: target },
  })

  return { action: 'created', id: created?.id, detail: '已创建每日 ' + time + ' 提醒' }
}
