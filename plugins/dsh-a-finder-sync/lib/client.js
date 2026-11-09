// Browser half of the a-finder sync plugin: a one-click "sync data" button.
//
// The button posts to the host route the host half registered, so clicking it
// runs exactly the same pipeline the a_finder_sync tool runs. No model turn is
// involved, which keeps the click fast and independent of the agent's state.
//
// The bundle is plain JavaScript on purpose: DSH serves the client bundle as
// written, and a build step would add a toolchain the plugin does not need.
// React comes from the shared module table the shell already provides.

window.__ModuleLoader__.load({
  id: 'dsh-a-finder-sync',
  factory: (require) => {
    const react = require('react')
    const { useCallback, useEffect, useRef, useState } = react

    const API_BASE = '/api/a-finder'

    /**
     * The one-click sync button.
     *
     * @returns a React element.
     */
    function SyncButton() {
      const [state, setState] = useState('idle')
      const [message, setMessage] = useState('')
      const [dates, setDates] = useState({ latest: '', published: '' })
      const mounted = useRef(true)

      useEffect(() => () => { mounted.current = false }, [])

      const loadStatus = useCallback(async () => {
        try {
          const response = await fetch(API_BASE + '/status', { headers: { accept: 'application/json' } })
          if (!response.ok) return
          const data = await response.json()
          if (!mounted.current) return
          setDates({ latest: data.latest ?? '', published: data.published ?? '' })
        } catch {
          // The status line is decoration: a failed read must not break the button.
        }
      }, [])

      useEffect(() => {
        void loadStatus()
      }, [loadStatus])

      const run = useCallback(async () => {
        if (state === 'running') return
        setState('running')
        setMessage('正在同步行情并推送…')
        try {
          const response = await fetch(API_BASE + '/sync', {
            method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({ push: true }),
          })
          const data = await response.json().catch(() => ({}))
          if (!mounted.current) return
          setMessage(data.message ?? (response.ok ? '同步完成' : '同步失败'))
          setState(response.ok && data.ok ? 'done' : 'error')
          if (data.latest || data.published) {
            setDates({ latest: data.latest ?? '', published: data.published ?? '' })
          }
        } catch (error) {
          if (!mounted.current) return
          setMessage('同步请求失败：' + (error instanceof Error ? error.message : String(error)))
          setState('error')
        }
      }, [state])

      const label = state === 'running' ? '同步中…' : state === 'done' ? '已同步' : state === 'error' ? '重试同步' : '同步数据'
      const hint = dates.latest
        ? '库 ' + dates.latest + ' · 站点 ' + (dates.published || '—')
        : ''

      return react.createElement(
        'div',
        {
          style: { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px' },
          'data-a-finder-sync': 'true',
        },
        react.createElement(
          'button',
          {
            type: 'button',
            onClick: () => { void run() },
            disabled: state === 'running',
            title: '同步 a-finder 沪深300 行情、重算选股并推送到 gh-pages',
            style: {
              cursor: state === 'running' ? 'progress' : 'pointer',
              padding: '2px 10px',
              borderRadius: '6px',
              border: '1px solid var(--dsh-border, rgba(0,0,0,0.15))',
              background: 'var(--dsh-surface, transparent)',
              color: 'inherit',
              font: 'inherit',
            },
          },
          label,
        ),
        hint ? react.createElement('span', { style: { opacity: 0.6 } }, hint) : null,
        message ? react.createElement('span', { style: { opacity: 0.75 } }, message) : null,
        'div',
      )
    }

    /** Required service: the UI slot registry. */
    const inject = ['slots']

    /**
     * Fill the composer dock slot with the sync button.
     *
     * @param ctx - Client root context.
     */
    function apply(ctx) {
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
