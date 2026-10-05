import { useCallback, useEffect, useState } from 'react'
import Chat from './components/Chat'
import Bookshelf from './components/Bookshelf'
import { fetchSessions } from './api'
import type { SessionInfo } from './types'

type View = 'chat' | 'bookshelf'

function newThreadId() {
  return `web-${crypto.randomUUID().slice(0, 8)}`
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diffMin = Math.round((Date.now() - t) / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHour = Math.round(diffMin / 60)
  if (diffHour < 24) return `${diffHour} 小时前`
  const d = new Date(t)
  return `${d.getMonth() + 1}-${d.getDate()}`
}

export default function App() {
  const [view, setView] = useState<View>('chat')
  const [threadId, setThreadId] = useState(newThreadId)
  const [sessions, setSessions] = useState<SessionInfo[]>([])

  const refreshSessions = useCallback(() => {
    fetchSessions()
      .then((r) => setSessions(r.sessions))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    refreshSessions()
  }, [refreshSessions])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-logo">小T</span>
          <span className="brand-name">教材答疑</span>
        </div>

        <nav className="nav">
          <button
            className={view === 'chat' ? 'nav-item active' : 'nav-item'}
            onClick={() => setView('chat')}
          >
            💬 问答
          </button>
          <button
            className={view === 'bookshelf' ? 'nav-item active' : 'nav-item'}
            onClick={() => setView('bookshelf')}
          >
            📚 书架
          </button>
        </nav>

        {view === 'chat' && (
          <div className="sessions">
            <div className="sessions-head">
              <span>历史会话</span>
              <button
                className="new-chat"
                onClick={() => setThreadId(newThreadId())}
                title="新会话"
              >
                ＋ 新对话
              </button>
            </div>
            <div className="session-list">
              {sessions.length === 0 && (
                <div className="session-empty">暂无历史会话</div>
              )}
              {sessions.map((s) => (
                <button
                  key={s.thread_id}
                  className={
                    s.thread_id === threadId
                      ? 'session-item active'
                      : 'session-item'
                  }
                  onClick={() => setThreadId(s.thread_id)}
                  title={s.thread_id}
                >
                  <span className="session-title">{s.title || s.thread_id}</span>
                  <span className="session-meta">
                    {formatRelativeTime(s.updated_at) || `${s.checkpoints} 轮记录`}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </aside>

      <main className="main">
        {view === 'chat' ? (
          <Chat threadId={threadId} onThreadUpdated={refreshSessions} />
        ) : (
          <Bookshelf />
        )}
      </main>
    </div>
  )
}
