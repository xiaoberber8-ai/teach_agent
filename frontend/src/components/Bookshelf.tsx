import { useEffect, useRef, useState } from 'react'
import type { Book, BookStatus } from '../types'
import { fetchBooks, uploadBook } from '../api'

const STATUS_META: Record<BookStatus, { label: string; cls: string; pulse?: boolean }> = {
  uploaded: { label: '已上传·排队中', cls: 'badge-queued', pulse: true },
  parsed: { label: '解析完成·向量化中', cls: 'badge-queued', pulse: true },
  indexed: { label: '已索引', cls: 'badge-indexed' },
  failed: { label: '入库失败', cls: 'badge-failed' },
}

export default function Bookshelf() {
  const [books, setBooks] = useState<Book[]>([])
  const [title, setTitle] = useState('')
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = async () => {
    const { books: list } = await fetchBooks()
    setBooks(list)
  }

  // 存在未完成的书时 2s 轮询状态
  useEffect(() => {
    load()
    const pending = books.some((b) => b.status === 'uploaded' || b.status === 'parsed')
    if (!pending) return
    const timer = setInterval(load, 2000)
    return () => clearInterval(timer)
  }, [books.some((b) => b.status === 'uploaded' || b.status === 'parsed')])

  const onUpload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setNotice('请先选择 PDF 或 TXT 文件')
      return
    }
    setUploading(true)
    setNotice(null)
    try {
      const res = await uploadBook(file, title)
      setNotice(
        res.duplicated
          ? `《${file.name}》已在库中（${res.message}）`
          : `已提交入库：${file.name}，解析+向量化期间可继续提问已完成的教材`,
      )
      setTitle('')
      if (fileRef.current) fileRef.current.value = ''
      await load()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="bookshelf">
      <h2>书架</h2>

      <div className="upload-card">
        <div className="upload-row">
          <input ref={fileRef} type="file" accept=".pdf,.txt" />
          <input
            className="title-input"
            type="text"
            placeholder="书名（可空，默认取文件名）"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button onClick={onUpload} disabled={uploading}>
            {uploading ? '提交中…' : '上传并入库'}
          </button>
        </div>
        {notice && <div className="upload-notice">{notice}</div>}
        <div className="upload-hint">支持 PDF / TXT；扫描版 PDF 暂不支持（M6 上 OCR）</div>
      </div>

      <div className="book-list">
        {books.length === 0 && <div className="empty-hint">书架空空，先上传一本教材吧</div>}
        {books.map((book) => {
          const meta = STATUS_META[book.status]
          return (
            <div key={book.book_id} className="book-item">
              <div className="book-main">
                <span className="book-title">《{book.title}》</span>
                <span className={`badge ${meta.cls}${meta.pulse ? ' pulse' : ''}`}>
                  {meta.label}
                </span>
              </div>
              <div className="book-meta">
                {book.page_count > 0 && `${book.page_count} 页`}
                {book.char_count > 0 && ` · ${book.char_count.toLocaleString()} 字符`}
                {book.status === 'failed' && book.error && (
                  <span className="book-error" title={book.error}> · {book.error}</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
