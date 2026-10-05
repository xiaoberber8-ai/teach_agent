"""M3 Web 后端：FastAPI。

提供四类接口：
- GET  /api/books              书架（含 uploaded/parsed/indexed/failed 四态）
- POST /api/books/upload       上传 PDF/TXT，后台线程跑完整入库流水线
- GET  /api/sessions           历史会话（读 SqliteSaver 落盘的 checkpoints）
- POST /api/chat/stream        SSE 流式问答（token + 工具调用过程）

开发：uv run uvicorn teach_agent.server:app --port 8000，前端 Vite 代理 /api。
生产：frontend/ 构建后，根路径自动托管 frontend/dist（存在时）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from . import config
from .agent import build_async_agent
from .config import SUPPORTED_SUFFIXES
from .indexing import clean_source_title, ingest_book, sha256_file
from .sessions import list_sessions
from .store import open_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时构建异步图（AsyncSqliteSaver 连接随之建立）
    app.state.graph = await build_async_agent()
    try:
        yield
    finally:
        # 收尾异步 sqlite 连接，避免 aiosqlite "connection never closed" 警告
        await app.state.graph.checkpointer.conn.close()


app = FastAPI(title="teach_agent API", version="0.3.0", lifespan=lifespan)

# 入库是重 CPU/GPU 任务（embedding 整本），全局串行，避免并发抢显存
_ingest_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
_UPLOAD_DIR = config.DATA_DIR / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 书架 ----

def _book_to_dict(book) -> dict:
    return {
        "book_id": book.book_id,
        "title": book.title,
        "status": book.status,
        "page_count": book.page_count,
        "char_count": book.char_count,
        "error": book.error,
        "created_at": book.created_at,
    }


@app.get("/api/books")
def api_books() -> dict:
    with open_store() as store:
        books = store.list_books()
    return {"books": [_book_to_dict(book) for book in books]}


def _run_ingest(path: Path, title: str | None, reindex: bool) -> None:
    """后台线程：异常已被 ingest_book 内部写进 books.status=failed。"""
    try:
        ingest_book(path, reindex=reindex, title_override=title)
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底，失败原因供前端展示
        # ScannedPDFError 等流水线内已 update(failed) 的情况会重复更新一次，
        # 但 error 文案一致，无害；未预期错误在此补齐 failed 状态。
        try:
            file_hash = sha256_file(path)
            with open_store() as store:
                existing = store.get_by_hash(file_hash)
                if existing and existing.status != "indexed":
                    store.update(existing.book_id, status="failed", error=str(exc))
        except Exception:
            pass


@app.post("/api/books/upload")
async def api_upload(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    reindex: bool = Form(default=False),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {suffix or '(无扩展名)'}，仅支持 "
            + "、".join(sorted(SUPPORTED_SUFFIXES)),
        )

    # 先落临时文件再算 hash；book_id = sha256 前 12 位（与入库流水线一致）
    temp_path = _UPLOAD_DIR / f"upload-{uuid.uuid4().hex}{suffix}"
    content = await file.read()
    if len(content) > config.MAX_FILE_MB * 1024 * 1024:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="文件超过大小上限")
    temp_path.write_bytes(content)

    digest = hashlib.sha256(content).hexdigest()
    book_id = digest[:12]
    final_title = title.strip() or clean_source_title(Path(file.filename).stem)

    # 重命名为稳定文件名，便于排查；同 hash 重传直接覆盖
    stable_path = _UPLOAD_DIR / f"{book_id}{suffix}"
    if stable_path != temp_path:
        temp_path.replace(stable_path)

    with open_store() as store:
        existing = store.get_by_hash(digest)
        if existing and existing.status == "indexed" and not reindex:
            return {
                "book_id": book_id,
                "status": existing.status,
                "duplicated": True,
                "message": "该书已入库，无需重复上传",
            }

    _ingest_executor.submit(_run_ingest, stable_path, final_title, reindex)
    return {"book_id": book_id, "status": "uploaded", "duplicated": False}


# ---------------------------------------------------------------- 会话 ----

@app.get("/api/sessions/{thread_id}/messages")
async def api_session_messages(thread_id: str, request: Request) -> dict:
    """回放指定会话的可见消息（human/ai 文本），用于切换历史会话时恢复界面。

    工具消息与只含 tool_calls 的中间 AIMessage 不返回——它们是推理过程，
    最终回答已在最后一条带内容的 AIMessage 中。
    """
    try:
        state = await request.app.state.graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"会话不存在或无法读取：{exc}")

    messages = []
    for msg in (state.values or {}).get("messages", []):
        msg_type = getattr(msg, "type", msg.__class__.__name__.lower())
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = str(content)
        if msg_type == "human":
            messages.append({"role": "user", "content": content})
        elif msg_type == "ai" and content.strip():
            messages.append({"role": "assistant", "content": content})
    return {"messages": messages}


async def _session_summary(graph, thread_id: str, checkpoints: int, last_cp: str) -> dict:
    """读最新状态，提取首条用户消息作标题与最近活跃时间。"""
    title = ""
    updated_at = None
    try:
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        updated_at = getattr(snap, "created_at", None)
        for msg in (snap.values or {}).get("messages", []):
            if getattr(msg, "type", msg.__class__.__name__.lower()) == "human":
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content.strip():
                    title = content.strip().splitlines()[0][:20]
                break
    except Exception:  # noqa: BLE001 - 标题失败不影响列表展示
        pass
    return {
        "thread_id": thread_id,
        "title": title,
        "checkpoints": checkpoints,
        "updated_at": updated_at,
        "last_checkpoint_id": last_cp,
    }


@app.get("/api/sessions")
async def api_sessions(request: Request) -> dict:
    infos = list_sessions(limit=50)
    sessions = await asyncio.gather(
        *[
            _session_summary(request.app.state.graph, i.thread_id, i.turns, i.last_checkpoint_id)
            for i in infos
        ]
    )
    return {"sessions": sessions}


# ---------------------------------------------------------------- 问答 ----

class ChatRequest(BaseModel):
    message: str
    thread_id: str


async def _stream_chat(graph, thread_id: str, message: str):
    # tags 带进 LangSmith，可在平台区分 cli / web 入口
    graph_config = {
        "configurable": {"thread_id": thread_id},
        "tags": ["teach-agent", "web"],
    }
    tool_seq = 0

    def encode(event: dict) -> bytes:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    try:
        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config=graph_config,
            version="v2",
        ):
            kind = event.get("event")
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                text = getattr(chunk, "content", "") if chunk else ""
                if isinstance(text, list):  # 极少数 provider 返回 content block 列表
                    text = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in text
                    )
                if text:
                    yield encode({"type": "token", "text": text})
            elif kind == "on_tool_start":
                tool_seq += 1
                yield encode(
                    {
                        "type": "tool_start",
                        "id": str(tool_seq),
                        "name": event.get("name", "tool"),
                    }
                )
            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output")
                text_out = getattr(output, "content", None)
                if text_out is None:
                    text_out = str(output)
                yield encode(
                    {
                        "type": "tool_end",
                        "id": str(tool_seq),
                        "name": event.get("name", "tool"),
                        "output": str(text_out)[:4000],
                    }
                )
        yield encode({"type": "done"})
    except Exception as exc:  # noqa: BLE001 - SSE 内异常要送到前端而非静默断流
        yield encode({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest, request: Request):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    return StreamingResponse(
        _stream_chat(request.app.state.graph, req.thread_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-transform", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------ 生产静态托管 ----

_DIST_DIR = config.PROJECT_ROOT / "frontend" / "dist"
if _DIST_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_DIST_DIR / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}")
    def spa_entry(full_path: str):  # noqa: ANN202
        candidate = _DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST_DIR / "index.html")
