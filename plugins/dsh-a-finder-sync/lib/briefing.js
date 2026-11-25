// Briefing generation: build prompt, validate output, deliver via Schedule.
//
// After performSync completes, index.js checks data/briefing-input.json.
// If it exists and is_trading_day == true and date matches DB latest,
// this module delivers a one-shot prompt to the DSH session so the agent
// can generate LLM narrative and write site/data/briefing-YYYY-MM-DD.json.

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

/**
 * Decide whether to generate a briefing for this sync run.
 * @param {object} input - Parsed briefing-input.json
 * @param {string} dbLatest - Newest trade_date from the database
 * @returns {boolean}
 */
export function shouldGenerateBriefing(input, dbLatest) {
  if (!input || typeof input !== 'object') return false
  if (input.is_trading_day !== true) return false
  if (!input.date) return false
  if (dbLatest && input.date !== dbLatest) return false
  return true
}

/**
 * Build the prompt delivered to the agent session.
 * @param {object} input - Parsed briefing-input.json
 * @param {string} projectDir - Repository root
 * @returns {string}
 */
export function buildBriefingPrompt(input, projectDir) {
  const dataJson = JSON.stringify(input, null, 1)
  return [
    '你是 a-finder 每日简报生成器。根据以下市场数据，生成一份时间流简报。',
    '',
    '项目目录：' + projectDir,
    '',
    '要求：',
    '1. 「当日盘中节奏」：按时间顺序写 5-7 个节点（intraday_nodes），每个节点一句话解读',
    '   （指出资金行为的方向、量级、与常态的对比）。不要罗列数字，要讲「发生了什么」。',
    '2. 「近期事件回顾」：最近 5 个交易日里最值得注意的 3-4 件事（events），每件一句话。',
    '3. 「选股亮点」：挑 3 只最有代表性的票（picks_highlight），说明为什么值得注意。',
    '4. 语气：客观、简洁、有洞察。不用感叹号，不用「值得关注」这类空话。',
    '5. 输出严格 JSON，写入文件 ' + join(projectDir, 'site', 'data', 'briefing-' + input.date + '.json'),
    '',
    '输出 schema：',
    '{',
    '  "date": "' + input.date + '",',
    '  "intraday_nodes": [{"time": "HH:MM", "text": "一句话解读"}],',
    '  "events": [{"date": "YYYY-MM-DD", "type": "类型", "detail": "一句话"}],',
    '  "picks_highlight": [{"code": "...", "name": "...", "strategy": "...", "day_pct": 0.0, "reason": "为什么值得注意"}]',
    '}',
    '',
    '数据：',
    dataJson,
  ].join('\n')
}

/**
 * Validate the agent's output before writing.
 * @param {object} output - Parsed JSON from agent
 * @param {string} expectedDate - The date we asked for
 * @returns {boolean}
 */
export function validateBriefingOutput(output, expectedDate) {
  if (!output || typeof output !== 'object') return false
  if (output.date !== expectedDate) return false
  if (!Array.isArray(output.intraday_nodes)) return false
  if (!Array.isArray(output.events)) return false
  if (!Array.isArray(output.picks_highlight)) return false
  return true
}

/**
 * Read briefing-input.json and deliver a one-shot task if conditions met.
 * @param {object} options
 * @param {object} options.service - DSH Schedule service
 * @param {string} options.projectDir - Repository root
 * @param {string} options.dbLatest - Newest trade_date from DB
 * @returns {Promise<{action: string, detail?: string}>}
 */
export async function deliverBriefingTask({ service, projectDir, dbLatest }) {
  const inputPath = join(projectDir, 'data', 'briefing-input.json')
  let input
  try {
    const raw = await readFile(inputPath, 'utf8')
    input = JSON.parse(raw)
  } catch {
    return { action: 'skipped', detail: 'briefing-input.json 不存在或不可读' }
  }

  if (!shouldGenerateBriefing(input, dbLatest)) {
    return { action: 'skipped', detail: '非交易日或日期不匹配' }
  }

  const prompt = buildBriefingPrompt(input, projectDir)
  const title = 'a-finder 简报生成 ' + input.date

  try {
    await service.create({
      title,
      prompt,
      timing: { kind: 'at', at: new Date().toISOString() },
      target: { kind: 'new_once' },
    })
    return { action: 'delivered', detail: '已投递简报生成任务 ' + input.date }
  } catch (error) {
    return { action: 'failed', detail: String(error) }
  }
}
