// 与后端 server.py / schema.py 对齐的类型定义

export type BookStatus = 'uploaded' | 'parsed' | 'indexed' | 'failed'

export interface Book {
  book_id: string
  title: string
  status: BookStatus
  page_count: number
  char_count: number
  error: string | null
  created_at: string
}

export interface SessionInfo {
  thread_id: string
  title: string
  checkpoints: number
  updated_at: string | null
  last_checkpoint_id: string
}

// 聊天消息（含流式过程中的工具调用与引用卡片）
export interface SourceCard {
  rank: number
  title: string
  chapter: string
  pages: string
  chunkId: string
  score: number
}

export interface ToolCall {
  id: string
  name: string
  output?: string
  sources?: SourceCard[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  tools?: ToolCall[]
  error?: string
}
