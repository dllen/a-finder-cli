// The actual a-finder sync: run the project's own pipeline script and report what happened.
//
// Why this shells out to a-finder.sh instead of re-implementing the sync in JS:
// the pipeline (sync -> picks -> export -> verify -> push) already has the
// monotonicity guard, the basis checks and the single-writer rules that the
// project depends on. Re-implementing any of it here would create a second,
// subtly different source of truth for the published site.
//
// The script runs in its own directory and writes its own log file, so a failed
// run leaves the published site untouched rather than half-written.

import { execFile, spawn } from 'node:child_process'
import { readFile, stat } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { promisify } from 'node:util'

/** How long one sync may run before we give up on it. */
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000

/** promisified execFile, for the small one-shot reads below. */
const execFileAsync = promisify(execFile)

/**
 * Read the newest date the local database has prices for.
 *
 * The database is the authority on what "the latest trading day" is: after a
 * weekend the newest row is the previous Friday, and a market holiday adds one
 * more day of staleness. Comparing against a hardcoded "today" would report a
 * false gap on every non-trading day.
 *
 * @param {string} dbPath - Path to the SQLite database.
 * @returns {Promise<string>} The newest trade date, or '' when unreadable.
 */
export async function readLatestTradeDate(dbPath) {
  const { stdout } = await execFileAsync('sqlite3', [dbPath, 'SELECT MAX(trade_date) FROM daily_prices;'], {
    encoding: 'utf8',
  })
  return stdout.trim()
}

/**
 * Read the newest date the published site carries.
 *
 * @param {string} siteDataDir - Directory holding the exported JSON.
 * @returns {Promise<string>} The newest published date, or '' when unreadable.
 */
export async function readPublishedDate(siteDataDir) {
  try {
    const raw = await readFile(join(siteDataDir, 'dates.json'), 'utf8')
    const dates = JSON.parse(raw)?.dates ?? []
    return typeof dates[0] === 'string' ? dates[0] : ''
  } catch {
    return ''
  }
}

/**
 * Run a-finder.sh site ... --push and collect its output.
 *
 * The child inherits stdio pipes rather than the parent's, so a long sync never
 * blocks the host event loop: this runs inside the DSH host process, which
 * serves the Web GUI.
 *
 * @param {object} options - Run options.
 * @param {string} options.projectDir - Repository root holding a-finder.sh.
 * @param {string} options.db - Database file name inside the repository.
 * @param {number} options.top - Number of picks per board.
 * @param {string} options.out - Export directory name inside the repository.
 * @param {boolean} options.push - Whether to publish to gh-pages.
 * @param {number} options.timeoutMs - Hard limit for the whole run.
 * @returns {Promise<{ok: boolean, code: number, output: string, timedOut: boolean}>} Run result.
 */
export function runSitePipeline(options) {
  const {
    projectDir,
    db = 'hs300.db',
    top = 20,
    out = 'site',
    push = true,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options

  const args = ['a-finder.sh', 'site', db, String(top), out]
  if (push) args.push('--push')

  return new Promise((resolvePromise) => {
    const child = spawn('/bin/bash', args, {
      cwd: projectDir,
      env: { ...process.env, TZ: process.env.TZ ?? 'Asia/Shanghai' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let output = ''
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      child.kill('SIGTERM')
      // A sync that ignores SIGTERM (it waits on network calls) must still die,
      // or the host keeps a half-finished pipeline alive for ever.
      setTimeout(() => child.kill('SIGKILL'), 10_000).unref?.()
    }, timeoutMs)
    timer.unref?.()

    const append = (chunk) => {
      output += String(chunk)
      // Keep the buffer bounded: a full pipeline prints a few thousand lines at
      // most, but a runaway loop must not grow this without limit.
      if (output.length > 400_000) output = output.slice(-200_000)
    }
    child.stdout?.on('data', append)
    child.stderr?.on('data', append)

    child.on('error', (error) => {
      clearTimeout(timer)
      resolvePromise({ ok: false, code: -1, output: output + String(error), timedOut })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolvePromise({ ok: code === 0 && !timedOut, code: code ?? -1, output, timedOut })
    })
  })
}

/**
 * Resolve the configured project directory.
 *
 * @param {string} configured - Configured project root.
 * @returns {Promise<string>} The absolute project root.
 */
export async function resolveProjectDir(configured) {
  const candidate = resolve(configured)
  try {
    const info = await stat(join(candidate, 'a-finder.sh'))
    if (!info.isFile()) throw new Error('a-finder.sh is not a file')
  } catch {
    throw new Error(
      `a-finder.sh not found under "${candidate}". Set the plugin config "projectDir" to the a-finder-cli repository root.`,
    )
  }
  return candidate
}

/**
 * Summarise a pipeline run into the text the agent and the button both show.
 *
 * @param {object} result - Result of runSitePipeline.
 * @param {string} latest - Newest date the database now carries.
 * @param {string} published - Newest date the site now carries.
 * @returns {string} A short human-readable summary.
 */
export function summarize(result, latest, published) {
  if (result.timedOut) {
    return '同步超时（超过时限仍未结束），已终止。'
  }
  if (!result.ok) {
    const tail = result.output.split('\n').filter((line) => line.trim()).slice(-12).join('\n')
    return `同步失败（退出码 ${result.code}）。最后输出：\n${tail}`
  }
  const parts = ['同步完成']
  if (latest) parts.push(`数据库最新交易日 ${latest}`)
  if (published) parts.push(`站点最新日期 ${published}`)
  return parts.join('，') + '。'
}
