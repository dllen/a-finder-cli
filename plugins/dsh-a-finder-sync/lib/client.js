// Browser half of the a-finder sync plugin: a one-click "sync data" pill.
//
// The button posts to the host route the host half registered, so clicking it
// runs exactly the same pipeline the a_finder_sync tool runs. No model turn is
// involved, which keeps the click fast and independent of the agent's state.
//
// The bundle is plain JavaScript on purpose: DSH serves the client bundle as
// written, and a build step would add a toolchain the plugin does not need.
// React comes from the shared module table the shell already provides.
//
// Styling matches the composer dock's own readings (see ContextMeter and the
// StatsPills in ui-conversation/ui-chat): 13px secondary text on a 20px line,
// an 8px-radius transparent pill that gains a hover fill, and tertiary labels.
// Keeping the pill on those tokens is what makes it sit beside "42% context"
// without looking bolted on. Class names are prefixed because the style sheet
// is inserted into the page once, not scoped by a bundler.

window.__ModuleLoader__.load({
  id: 'dsh-a-finder-sync',
  factory: (require) => {
    const react = require('react')
    const { useCallback, useEffect, useRef, useState } = react

    const API_BASE = '/api/a-finder'

    // The dock already has a 22px row height (20px line + 1px padding on the
    // host pills). These rules deliberately do not set a height: the padding
    // and inherited line-height produce the same 22px box, so the plugin can
    // never make the dock taller than the context meter beside it.
    const STYLES = `
.afs-root {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  max-width: 100%;
  color: var(--dsw-alias-label-tertiary);
  font-family: var(--dsw-font-family, inherit);
  font-size: var(--dsh-content-font-size-secondary, 13px);
  line-height: calc(20px + var(--dsh-content-font-delta-secondary, 0px));
}

.afs-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  box-sizing: border-box;
  padding: 1px 8px;
  border: 0;
  border-radius: var(--dsw-radius-sm, 8px);
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 100ms ease, color 100ms ease;
}

@media (hover: hover) {
  .afs-button:hover:not(:disabled) {
    background: var(--dsw-alias-interactive-bg-hover);
    color: var(--dsw-alias-label-secondary);
  }
}

.afs-button:active:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-active);
}

.afs-button:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: 1px;
}

/* Running is progress, not a disabled control: keep the label readable while
   the pointer tells the user the click is already being handled. */
.afs-button:disabled {
  opacity: 1;
  cursor: progress;
}

.afs-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 14px;
  height: 14px;
}

.afs-root[data-state='running'] .afs-icon {
  color: var(--dsw-alias-state-business-primary);
}

.afs-root[data-state='done'] .afs-icon {
  color: var(--dsw-alias-state-success-primary);
}

.afs-root[data-state='error'] .afs-icon {
  color: var(--dsw-alias-state-error-primary);
}

.afs-label {
  min-width: 0;
}

.afs-elapsed {
  color: var(--dsw-alias-label-caption);
  font-variant-numeric: tabular-nums;
}

/* The status line is a reading, not a control: it never gets a hover fill and
   it ellipsizes rather than wrapping, because a wrapped line would make the
   composer jump while the user is typing. */
.afs-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  position: relative;
  min-width: 0;
  max-width: 34ch;
  padding: 1px 4px;
  color: var(--dsw-alias-label-tertiary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.afs-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  corner-shape: round;
  background: var(--dsw-alias-state-idle-primary);
}

.afs-dot[data-tone='ok'] {
  background: var(--dsw-alias-state-success-primary);
}

.afs-dot[data-tone='warn'] {
  background: var(--dsw-alias-state-warn-primary);
}

.afs-dot[data-tone='error'] {
  background: var(--dsw-alias-state-error-primary);
}

.afs-status-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Below a phone-sized composer the dock can only afford the signal, not the
   sentence. Keep the colored dot (fresh / behind / failed) and clip the text
   visually instead of removing it, so it stays the status region's accessible
   name and the tooltip still carries the dates. */
@media (max-width: 600px) {
  .afs-status {
    max-width: none;
    padding: 1px 2px;
  }

  .afs-status-text {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }
}

.afs-spin {
  animation: afs-rotate 800ms linear infinite;
  transform-origin: center;
}

@media (prefers-reduced-motion: reduce) {
  .afs-spin {
    animation-duration: 2400ms;
  }
}

@keyframes afs-rotate {
  to {
    transform: rotate(360deg);
  }
}
`

    const Svg = (props, ...children) => react.createElement('svg', props, ...children)

    /** The idle action: a refresh arrow, the conventional "sync now" glyph. */
    const RefreshIcon = () => Svg({
      viewBox: '0 0 24 24',
      width: 14,
      height: 14,
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: 2.2,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      'aria-hidden': true,
      focusable: 'false',
    },
    react.createElement('path', { d: 'M21 12a9 9 0 1 1-2.64-6.36' }),
    react.createElement('path', { d: 'M21 3v6h-6' }))

    /** Running: the same arc the composer's send button uses while sending. */
    const SpinnerIcon = () => Svg({
      viewBox: '0 0 16 16',
      width: 14,
      height: 14,
      fill: 'none',
      'aria-hidden': true,
      focusable: 'false',
      className: 'afs-spin',
    },
    react.createElement('circle', {
      cx: 8, cy: 8, r: 6, stroke: 'currentColor', strokeOpacity: 0.25, strokeWidth: 2,
    }),
    react.createElement('path', {
      d: 'M8 2a6 6 0 0 1 6 6',
      stroke: 'currentColor',
      strokeWidth: 2,
      strokeLinecap: 'round',
    }))

    /** Done: success is the one state that earns a checkmark instead of a verb. */
    const CheckIcon = () => Svg({
      viewBox: '0 0 24 24',
      width: 14,
      height: 14,
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: 2.4,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      'aria-hidden': true,
      focusable: 'false',
    },
    react.createElement('path', { d: 'M20 6 9 17l-5-5' }))

    /** Error: a circle with a bang reads at 14px, where a triangle gets muddy. */
    const AlertIcon = () => Svg({
      viewBox: '0 0 24 24',
      width: 14,
      height: 14,
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: 2.2,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      'aria-hidden': true,
      focusable: 'false',
    },
    react.createElement('circle', { cx: 12, cy: 12, r: 9 }),
    react.createElement('path', { d: 'M12 8v4.8' }),
    react.createElement('circle', { cx: 12, cy: 16.4, r: 1.1, fill: 'currentColor', stroke: 'none' }))

    /**
     * Keep the date short enough to fit one dock line, but never lose the year
     * when the data is older than the current one.
     *
     * @param {string} value - YYYY-MM-DD.
     * @returns {string} MM-DD for this year, YY-MM-DD otherwise.
     */
    function formatDate(value) {
      const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '')
      if (match === null) return value ?? ''
      const year = String(new Date().getFullYear())
      return match[1] === year
        ? match[2] + '-' + match[3]
        : match[1].slice(2) + '-' + match[2] + '-' + match[3]
    }

    /**
     * One compact reading for the three dates the host knows.
     *
     * Bond data can legitimately be absent (the source is a realtime snapshot
     * and an old database has no cb table), so an empty bond date is omitted
     * instead of rendered as a dash that reads like a failure.
     *
     * @param {object} dates - { latest, published, cb, etf }.
     * @returns {string} "行情 11-19 · 站点 11-19", with bond/ETF segments only when they lag.
     */
    function dateSummary(dates) {
      const parts = []
      if (dates.latest) parts.push('行情 ' + formatDate(dates.latest))
      // The bond board normally carries the same day as the price table; only
      // its lag is worth a third segment in the dock. The tooltip always lists
      // the bond date, so hiding the duplicate loses nothing.
      if (dates.cb && dates.cb !== dates.latest) parts.push('转债 ' + formatDate(dates.cb))
      // Same rule for the ETF board: it is fetched from an external snapshot,
      // so a same-day value is the norm and only a lag earns dock width.
      if (dates.etf && dates.etf !== dates.latest) parts.push('ETF ' + formatDate(dates.etf))
      if (dates.published) parts.push('站点 ' + formatDate(dates.published))
      return parts.join(' · ')
    }

    /**
     * The dot's meaning: green when the site carries the database's newest
     * trading day, amber when it is behind, neutral when there is nothing to
     * compare yet. A stale site is exactly the failure the sync exists to fix,
     * so it gets a color instead of being left as plain text.
     *
     * @param {object} dates - { latest, published }.
     * @returns {'ok'|'warn'|'neutral'} The dot tone.
     */
    function freshnessTone(dates) {
      if (!dates.latest) return dates.published ? 'ok' : 'neutral'
      if (!dates.published) return 'warn'
      return dates.published >= dates.latest ? 'ok' : 'warn'
    }

    /**
     * Turn a pipeline summary into one dock-width sentence.
     *
     * The host summary is deliberately detailed and can span a dozen lines of
     * shell output. Repeating that in the dock would push the composer around,
     * so the detail stays in the hover title and the log; the visible line
     * keeps only the exit code.
     *
     * @param {string} message - The host's summary.
     * @returns {string} A short status line.
     */
    function describeError(message) {
      const lines = String(message ?? '').split('\n').map((line) => line.trim()).filter(Boolean)
      const first = lines[0] ?? ''
      if (first === '') return '同步失败'
      const code = /退出码\s*(-?\d+)/.exec(first)
      if (code !== null) return '同步失败 · 退出码 ' + code[1]
      return first.length > 60 ? first.slice(0, 59) + '…' : first
    }

    /**
     * mm:ss, or h:mm:ss for a run past an hour. A sync normally takes 15–35
     * minutes, so a bare spinner would leave the user guessing for a long time.
     *
     * @param {number} ms - Elapsed milliseconds.
     * @returns {string} The elapsed clock.
     */
    function formatElapsed(ms) {
      const total = Math.max(0, Math.floor(ms / 1000))
      const minutes = Math.floor(total / 60)
      const seconds = total % 60
      if (minutes < 60) return minutes + ':' + String(seconds).padStart(2, '0')
      return Math.floor(minutes / 60) + ':' + String(minutes % 60).padStart(2, '0') + ':' + String(seconds).padStart(2, '0')
    }

    /** The icon that matches the current state. */
    function stateIcon(state) {
      if (state === 'running') return react.createElement(SpinnerIcon)
      if (state === 'done') return react.createElement(CheckIcon)
      if (state === 'error') return react.createElement(AlertIcon)
      return react.createElement(RefreshIcon)
    }

    /**
     * Install the dock styles once for the page, and remove them when the
     * plugin is disabled. Injecting through ctx.effect keeps the style sheet's
     * lifetime tied to the plugin instead of leaving an orphan <style> behind.
     *
     * @param {object} ctx - Client root context.
     * @returns {void}
     */
    function installStyles(ctx) {
      if (typeof document === 'undefined') return
      const install = () => {
        const style = document.createElement('style')
        style.setAttribute('data-dsh-plugin', 'a-finder-sync')
        style.textContent = STYLES
        document.head.appendChild(style)
        return () => style.remove()
      }
      if (typeof ctx.effect === 'function') ctx.effect(install)
      else install()
    }

    /**
     * The one-click sync pill.
     *
     * @returns a React element.
     */
    function SyncButton() {
      const [state, setState] = useState('idle')
      const [message, setMessage] = useState('')
      const [notice, setNotice] = useState('')
      const [dates, setDates] = useState({ latest: '', published: '', cb: '', etf: '' })
      const [elapsed, setElapsed] = useState(0)
      const mounted = useRef(true)
      const startedAt = useRef(0)

      useEffect(() => {
        mounted.current = true
        return () => { mounted.current = false }
      }, [])

      const loadStatus = useCallback(async () => {
        try {
          const response = await fetch(API_BASE + '/status', { headers: { accept: 'application/json' } })
          if (!response.ok) return
          const data = await response.json()
          if (!mounted.current) return
          setDates({ latest: data.latest ?? '', published: data.published ?? '', cb: data.cb ?? '', etf: data.etf ?? '' })
        } catch {
          // The status line is decoration: a failed read must not break the button.
        }
      }, [])

      useEffect(() => {
        void loadStatus()
      }, [loadStatus])

      // Restart the clock on every run so a retry does not inherit the previous
      // run's elapsed time. The interval is cleared the moment the state leaves
      // "running", including the busy and error paths.
      useEffect(() => {
        if (state !== 'running') {
          setElapsed(0)
          return undefined
        }
        startedAt.current = Date.now()
        setElapsed(0)
        const timer = setInterval(() => {
          if (mounted.current) setElapsed(Date.now() - startedAt.current)
        }, 1000)
        return () => clearInterval(timer)
      }, [state])

      const run = useCallback(async () => {
        if (state === 'running') return
        setState('running')
        setNotice('')
        setMessage('正在同步行情、可转债榜单与 ETF 看板并推送…')
        try {
          const response = await fetch(API_BASE + '/sync', {
            method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({ push: true, cb: true, etf: true }),
          })
          const data = await response.json().catch(() => ({}))
          if (!mounted.current) return

          // A second click (or an agent-triggered sync) is not a failure. Show
          // it as an informational reading and put the button back in its
          // idle pose so the user can try again once the first run finishes.
          if (data.busy === true) {
            setState('idle')
            setMessage(data.message ?? '已有同步在进行中。')
            setNotice('已有同步在进行中')
            return
          }

          if (response.ok && data.ok) {
            setState('done')
            setMessage(data.message ?? '同步完成')
            if (data.latest || data.published || data.cb || data.etf) {
              setDates({ latest: data.latest ?? '', published: data.published ?? '', cb: data.cb ?? '', etf: data.etf ?? '' })
            } else {
              void loadStatus()
            }
            return
          }

          const text = data.message ?? '同步失败（HTTP ' + response.status + '）'
          setMessage(text)
          setNotice(describeError(text))
          setState('error')
        } catch (error) {
          if (!mounted.current) return
          const text = '同步请求失败：' + (error instanceof Error ? error.message : String(error))
          setMessage(text)
          setNotice(describeError(text))
          setState('error')
        }
      }, [state, loadStatus])

      const label = state === 'running'
        ? '同步中'
        : state === 'done'
          ? '已同步'
          : state === 'error'
            ? '重试同步'
            : '同步数据'

      const dateText = dateSummary(dates)
      const tone = state === 'error'
        ? 'error'
        : notice
          ? 'warn'
          : freshnessTone(dates)
      const statusText = notice
        || (state === 'error' ? describeError(message) : dateText)
        || (state === 'running' ? '正在同步…' : '')

      // Everything the dock is too narrow to say stays one hover away: the full
      // ISO dates and the complete (often multi-line) pipeline output.
      const details = [
        dates.latest ? '行情最新交易日 ' + dates.latest : '',
        dates.cb ? '可转债榜单 ' + dates.cb : '',
        dates.etf ? 'ETF 看板数据 ' + dates.etf : '',
        dates.published ? '站点最新日期 ' + dates.published : '',
        message ? '最近一次：' + message : '',
      ].filter(Boolean).join('\n')

      const buttonTitle = state === 'running'
        ? '正在同步沪深300 行情、可转债榜单、ETF 看板与选股，并推送到 gh-pages（通常 15~35 分钟）'
        : state === 'done'
          ? '已同步；再次点击可重新同步'
          : state === 'error'
            ? '上次同步失败；点击重试。完整原因见悬停提示与 logs/a-finder-sync.log'
            : '同步沪深300 行情与可转债榜单、刷新 ETF 看板快照、重算选股并推送到 gh-pages'

      return react.createElement(
        'div',
        {
          className: 'afs-root',
          'data-state': state,
          'data-a-finder-sync': 'true',
        },
        react.createElement(
          'button',
          {
            type: 'button',
            className: 'afs-button',
            onClick: () => { void run() },
            disabled: state === 'running',
            title: buttonTitle,
            'aria-busy': state === 'running',
          },
          react.createElement('span', { className: 'afs-icon', 'aria-hidden': 'true' }, stateIcon(state)),
          react.createElement('span', { className: 'afs-label' }, label),
          state === 'running'
            ? react.createElement('span', { className: 'afs-elapsed', 'aria-hidden': 'true' }, formatElapsed(elapsed))
            : null,
        ),
        statusText
          ? react.createElement(
            'span',
            {
              className: 'afs-status',
              title: details,
              role: 'status',
              'aria-live': 'polite',
              'aria-atomic': 'true',
            },
            tone === 'neutral'
              ? null
              : react.createElement('span', { className: 'afs-dot', 'data-tone': tone, 'aria-hidden': 'true' }),
            react.createElement('span', { className: 'afs-status-text' }, statusText),
          )
          : null,
      )
    }

    /** Required service: the UI slot registry. */
    const inject = ['slots']

    /**
     * Fill the composer dock slot with the sync pill.
     *
     * @param ctx - Client root context.
     */
    function apply(ctx) {
      installStyles(ctx)
      ctx.slots.inject('conversation.composer.dock', () =>
        ctx.slots.register(
          {
            name: 'conversation.composer.dock',
            id: 'a-finder-sync',
            order: 10,
          },
          SyncButton,
        ),
      )
    }

    return { apply, inject }
  },
})
