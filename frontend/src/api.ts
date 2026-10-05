import type { Book, SessionInfo, SourceCard } from './types'

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`${url} -> ${resp.status}`)
  return resp.json() as Promise<T>
}

export function fetchBooks(): Promise<{ books: Book[] }> {
  return getJson('/api/books')
}

export function fetchSessions(): Promise<{ sessions: SessionInfo[] }> {
  return getJson('/api/sessions')
}

export interface ThreadMessageDTO {
  role: 'user' | 'assistant'
  content: string
}

export async function fetchThreadMessages(
  threadId: string,
): Promise<ThreadMessageDTO[]> {
  const url = `/api/sessions/${encodeURIComponent(threadId)}/messages`
  let lastErr: unknown
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const data = await getJson<{ messages: ThreadMessageDTO[] }>(url)
      return data.messages
    } catch (err) {
      // 后端重启后 Vite 代理可能残留旧 keep-alive 连接，首次请求 ECONNRESET；
      // 重试一次即可。仍失败则抛出，由界面给出"重试"而不是假装空会话。
      lastErr = err
    }
  }
  throw lastErr
}

export async function uploadBook(
  file: File,
  title: string,
  reindex = false,
): Promise<{ book_id: string; status: string; duplicated: boolean; message?: string }> {
  const form = new FormData()
  form.append('file', file)
  form.append('title', title)
  form.append('reindex', String(reindex))
  const resp = await fetch('/api/books/upload', { method: 'POST', body: form })
  const data = await resp.json()
  if (!resp.ok) throw new Error(data.detail || `上传失败 (${resp.status})`)
  return data
}

// search_book 工具输出解析：
// [1] 《书名》｜4.2.1 信息增益｜第91页｜相似度 0.680｜chunk_id=xxx:00160
const SOURCE_LINE =
  /^\[(\d+)\]\s*《(.+?)》｜([^｜]*)｜(第[\d\-]+页)(?:｜相似度\s*([\d.]+))?｜chunk_id=(\S+)/

export function parseSources(toolOutput: string): SourceCard[] {
  const cards: SourceCard[] = []
  for (const line of toolOutput.split('\n')) {
    const m = line.match(SOURCE_LINE)
    if (m) {
      cards.push({
        rank: Number(m[1]),
        title: m[2],
        chapter: m[3],
        pages: m[4],
        score: m[5] ? Number(m[5]) : 0,
        chunkId: m[6],
      })
    }
  }
  return cards
}

export type StreamEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_start'; id: string; name: string }
  | { type: 'tool_end'; id: string; name: string; output: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

/**
 * 以 POST + fetch 流读取 SSE（EventSource 不支持 POST）。
 * 每个回调处理一个服务端事件。
 */
export async function streamChat(
  threadId: string,
  message: string,
  onEvent: (evt: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, message }),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const detail = await resp.json().catch(() => null)
    throw new Error(detail?.detail || `问答请求失败 (${resp.status})`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)) as StreamEvent)
      } catch {
        // 半个 JSON 包，忽略等下一片
      }
    }
  }
}
