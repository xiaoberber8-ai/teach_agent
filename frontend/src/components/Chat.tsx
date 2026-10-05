import { useEffect, useRef, useState } from 'react'
import type { ChatMessage, ToolCall } from '../types'
import { fetchThreadMessages, parseSources, streamChat } from '../api'
import Markdown from './Markdown'

const TOOL_LABELS: Record<string, string> = {
  list_books: '查看书架',
  search_book: '检索教材原文',
  read_chunk: '读取完整片段',
  read_file: '读取教学技能',
  web_search: '联网检索',
}

export default function Chat({
  threadId,
  onThreadUpdated,
}: {
  threadId: string
  onThreadUpdated?: () => void
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [historyError, setHistoryError] = useState(false)
  const [retryTick, setRetryTick] = useState(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  // 切换会话（点击历史会话 / 新对话）：从 checkpoint 回放可见消息，
  // 而不是只清空界面——否则点击历史会话看起来"没有反应"。
  useEffect(() => {
    let cancelled = false
    setLoadingHistory(true)
    setHistoryError(false)
    fetchThreadMessages(threadId)
      .then((history) => {
        if (cancelled) return
        setMessages(
          history.map((m, idx) => ({
            id: `hist-${threadId}-${idx}`,
            role: m.role,
            content: m.content,
          })),
        )
        setLoadingHistory(false)
      })
      .catch(() => {
        if (cancelled) return
        setMessages([])
        setLoadingHistory(false)
        setHistoryError(true)
      })
    return () => {
      cancelled = true
    }
  }, [threadId, retryTick])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const send = async () => {
    const text = input.trim()
    if (!text || busy || loadingHistory) return
    setInput('')
    setBusy(true)

    const userId = crypto.randomUUID()
    const assistantId = crypto.randomUUID()
    setMessages((prev) => [
      ...prev,
      { id: userId, role: 'user', content: text },
      { id: assistantId, role: 'assistant', content: '', streaming: true, tools: [] },
    ])

    const patchAssistant = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)))

    const upsertTool = (tool: ToolCall) =>
      patchAssistant((m) => {
        const tools = [...(m.tools ?? [])]
        const idx = tools.findIndex((t) => t.id === tool.id)
        if (idx >= 0) tools[idx] = { ...tools[idx], ...tool }
        else tools.push(tool)
        return { ...m, tools }
      })

    try {
      await streamChat(threadId, text, (evt) => {
        if (evt.type === 'token') {
          patchAssistant((m) => ({ ...m, content: m.content + evt.text }))
        } else if (evt.type === 'tool_start') {
          upsertTool({ id: evt.id, name: evt.name })
        } else if (evt.type === 'tool_end') {
          upsertTool({
            id: evt.id,
            name: evt.name,
            output: evt.output,
            sources:
              evt.name === 'search_book' ? parseSources(evt.output) : undefined,
          })
        } else if (evt.type === 'error') {
          patchAssistant((m) => ({ ...m, error: evt.message }))
        }
      })
      patchAssistant((m) => ({ ...m, streaming: false }))
      onThreadUpdated?.()
    } catch (err) {
      patchAssistant((m) => ({
        ...m,
        streaming: false,
        error: err instanceof Error ? err.message : '请求失败',
      }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat">
      <div className="messages" ref={scrollRef}>
        {loadingHistory && messages.length === 0 && (
          <div className="chat-empty">正在加载历史记录…</div>
        )}
        {!loadingHistory && historyError && (
          <div className="chat-empty">
            <p>历史记录加载失败（网络或服务异常）。</p>
            <p>
              <button
                className="retry-btn"
                onClick={() => setRetryTick((t) => t + 1)}
              >
                点击重试
              </button>
            </p>
          </div>
        )}
        {!loadingHistory && !historyError && messages.length === 0 && (
          <div className="chat-empty">
            <p>你好，我是<strong>小T</strong>，你的教材答疑老师。</p>
            <p>可以问我书架中教材的概念、公式推导、习题，或让我带你复习一章。</p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg msg-${m.role}`}>
            {m.role === 'user' ? (
              <div className="bubble bubble-user">{m.content}</div>
            ) : (
              <div className="bubble bubble-ai">
                {m.tools && m.tools.length > 0 && (
                  <div className="tool-rail">
                    {m.tools.map((t) => (
                      <ToolChip key={t.id} tool={t} />
                    ))}
                  </div>
                )}
                {m.content && <Markdown>{m.content}</Markdown>}
                {m.streaming && !m.content && (
                  <span className="thinking">正在检索与思考…</span>
                )}
                {m.error && <div className="msg-error">调用失败：{m.error}</div>}
                {m.streaming && m.content && <span className="cursor">▍</span>}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="composer">
        <textarea
          value={input}
          placeholder="针对教材提问…（Enter 发送，Shift+Enter 换行）"
          rows={1}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <button onClick={send} disabled={busy || loadingHistory || !input.trim()}>
          {busy ? '…' : '发送'}
        </button>
      </div>
    </div>
  )
}

function ToolChip({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false)
  const label = TOOL_LABELS[tool.name] ?? tool.name
  const running = tool.output === undefined

  return (
    <div className="tool-chip">
      <button className="tool-head" onClick={() => setOpen((v) => !v)}>
        <span className={`tool-dot${running ? ' running' : ''}`} />
        {label}
        {tool.sources && tool.sources.length > 0 && (
          <span className="tool-count">{tool.sources.length} 条来源</span>
        )}
        <span className="tool-arrow">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="tool-body">
          {tool.sources && tool.sources.length > 0 ? (
            <div className="source-cards">
              {tool.sources.map((s) => (
                <div className="source-card" key={s.chunkId}>
                  <span className="source-book">《{s.title}》</span>
                  <span className="source-page">{s.pages}</span>
                  {s.chapter && <span className="source-chapter">{s.chapter}</span>}
                  <span className="source-score">相似度 {s.score.toFixed(2)}</span>
                </div>
              ))}
            </div>
          ) : (
            <pre className="tool-raw">{tool.output ?? '执行中…'}</pre>
          )}
        </div>
      )}
    </div>
  )
}
