"""M2 教材答疑 Agent 装配：DeepAgents + 本地 RAG 工具 + 持久会话 + Skills。

- LLM 走 OpenAI 兼容协议（.env 中配置，默认 DeepSeek 官方端点 deepseek-flash）；
- 禁用 DeepAgents 默认注入的 general-purpose 子代理（task 工具）；
- 文件工具只保留只读的 read_file，并把文件后端 jail 在 data/agent_fs/，
  模型只能读取其中的 skills/ 技能文件，无法触碰教材原文之外的宿主文件；
- checkpointer 使用 SqliteSaver（data/checkpoints.sqlite），同一 thread_id
  跨进程可恢复完整对话历史；
- 教学技能（概念讲解/公式推导/习题辅导/章节复习）放在 agent_fs/skills/，
  由 SkillsMiddleware 以渐进披露方式提供：系统提示只列技能名与描述，
  模型按需用 read_file 读取完整 SKILL.md。
"""

from __future__ import annotations

import shutil
import sqlite3
from functools import lru_cache

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from . import config
from .prompts import TEACH_SYSTEM_PROMPT
from .tools import list_books, read_chunk, search_book, web_search

# 写/执行类内置工具对教材答疑无用且扩大暴露面，全部剔除；
# read_file 保留：SkillsMiddleware 的渐进披露依赖模型自行读取 SKILL.md。
# 全集即 deepagents 0.7 的 FsToolName Literal（升级时需复核）：
# ['ls','read_file','write_file','edit_file','delete','glob','grep','execute']
_EXCLUDED_BUILTIN_TOOLS = frozenset(
    {
        "ls",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    }
)

_PROFILE_REGISTERED = False


def build_llm() -> ChatOpenAI:
    """按 .env 契约构造答疑模型；缺 key 时给出可操作的报错。"""
    if not config.LLM_API_KEY:
        raise RuntimeError(
            "缺少 TEACH_LLM_API_KEY：请在项目根目录 .env 中配置（参考 .env.example）。"
        )
    return ChatOpenAI(
        model=config.LLM_MODEL,
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        temperature=config.LLM_TEMPERATURE,
        timeout=120,
        max_retries=2,
    )


@lru_cache(maxsize=1)
def build_checkpointer() -> SqliteSaver:
    """进程级单例：磁盘 SqliteSaver，跨进程保留全部会话历史。

    check_same_thread=False：工具节点在线程池中执行，连接需跨线程共享；
    WAL 降低读写互斥；setup() 幂等建表。
    """
    config.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(config.CHECKPOINT_DB), check_same_thread=False
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def _sync_skills_into_jail() -> None:
    """把仓库 skills/ 的产品技能同步进 jail（内容变化才复制）。

    只增不改不删：jail 中用户自建的额外技能目录保留；仓库里被删除的旧技能
    不主动清理（避免误删用户同名定制）。
    """
    if not config.SKILLS_SOURCE_DIR.is_dir():
        return
    jail_skills = config.AGENT_FS_ROOT / config.SKILLS_DIR_NAME
    for src_file in config.SKILLS_SOURCE_DIR.rglob("*"):
        if not src_file.is_file():
            continue
        relative = src_file.relative_to(config.SKILLS_SOURCE_DIR)
        dst_file = jail_skills / relative
        if (
            not dst_file.exists()
            or dst_file.read_bytes() != src_file.read_bytes()
        ):
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)


async def build_async_checkpointer():
    """Web 专用：与 CLI 共享同一 sqlite 文件的异步 saver。

    astream/astream_events 只接受异步 checkpointer；CLI 的同步 saver
    与本 saver 通过 WAL 共存（一般不同时跑在一个进程里）。
    连接在 FastAPI lifespan 关闭时由 close_async_checkpointer 收尾。
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    config.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(config.CHECKPOINT_DB))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver


@lru_cache(maxsize=1)
def build_backend() -> FilesystemBackend:
    """文件后端：jail 在 data/agent_fs/（virtual_mode 阻断路径逃逸）。"""
    config.AGENT_FS_ROOT.mkdir(parents=True, exist_ok=True)
    (config.AGENT_FS_ROOT / config.SKILLS_DIR_NAME).mkdir(exist_ok=True)
    _sync_skills_into_jail()
    return FilesystemBackend(
        root_dir=config.AGENT_FS_ROOT,
        virtual_mode=True,
    )


def _ensure_profile() -> None:
    """为本模型注册 harness：关闭通用子代理、剔除写/执行类内置工具。"""
    global _PROFILE_REGISTERED
    if _PROFILE_REGISTERED:
        return
    profile = HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=_EXCLUDED_BUILTIN_TOOLS,
    )
    register_harness_profile(f"openai:{config.LLM_MODEL}", profile)
    _PROFILE_REGISTERED = True


def _assemble(model, checkpointer):
    """同步/异步装配共用：模型、工具、提示词、jail、skills 完全一致。"""
    return create_deep_agent(
        model=model,
        tools=[list_books, search_book, read_chunk, web_search],
        system_prompt=TEACH_SYSTEM_PROMPT,
        backend=build_backend(),
        skills=[config.SKILLS_DIR_NAME],
        checkpointer=checkpointer,
    )


def build_agent(checkpointer: SqliteSaver | None = None):
    """CLI 用：同步图 + 磁盘 SqliteSaver 单例。"""
    _ensure_profile()
    return _assemble(build_llm(), checkpointer or build_checkpointer())


async def build_async_agent():
    """Web 用：异步图 + AsyncSqliteSaver（同一 checkpoint 文件）。"""
    _ensure_profile()
    checkpointer = await build_async_checkpointer()
    return _assemble(build_llm(), checkpointer)
