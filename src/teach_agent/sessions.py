"""会话持久化辅助：跨进程列出/恢复 LangGraph checkpoint 会话。

checkpoint 表由 langgraph-checkpoint-sqlite 维护：
checkpoints(thread_id, checkpoint_ns, checkpoint_id, ..., metadata)
checkpoint_id 是 ULID（字典序即时间序），MAX(checkpoint_id) 即最近一次对话。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class SessionInfo:
    thread_id: str
    turns: int
    last_checkpoint_id: str


def list_sessions(limit: int = 20) -> list[SessionInfo]:
    """按最近活跃倒序列出会话（不反序列化消息体，零模型依赖）。"""
    if not config.CHECKPOINT_DB.exists():
        return []
    uri = f"file:{config.CHECKPOINT_DB}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            """
            SELECT thread_id, COUNT(*), MAX(checkpoint_id)
            FROM checkpoints
            GROUP BY thread_id
            ORDER BY MAX(checkpoint_id) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [SessionInfo(tid, n, last) for tid, n, last in rows]
