"""教材答疑命令行入口：多轮对话 REPL（会话持久化在 data/checkpoints.sqlite）。

用法：
  uv run python -m teach_agent.chat                 # 交互式：可选续聊旧会话
  uv run python -m teach_agent.chat --list          # 只列出历史会话
  uv run python -m teach_agent.chat --thread <id>   # 直接恢复指定会话

对话内命令：/new 开新会话；/sessions 列会话；/resume <id> 切换会话；/exit 退出。
历史落盘后，重开进程用同一 thread_id 即可接着聊。
"""

from __future__ import annotations

import argparse
import sys
import uuid

from langchain_core.messages import HumanMessage
from langchain_core.tracers.langchain import wait_for_all_tracers

from .agent import build_agent
from .sessions import list_sessions

_BANNER = """\
============================================================
 小T · 教材答疑助手（/new 新会话 /sessions 列表 /resume 续聊 /exit 退出）
============================================================
"""


def _print_sessions() -> None:
    sessions = list_sessions()
    if not sessions:
        print("（暂无历史会话）")
        return
    print("历史会话（最近在前）：")
    for idx, info in enumerate(sessions, start=1):
        print(f"  [{idx}] {info.thread_id}｜checkpoint {info.turns} 条")


def _choose_starting_thread() -> str:
    """交互启动菜单：编号续聊，回车开新会话；非 TTY 直接开新会话。"""
    if not sys.stdin.isatty():
        return f"cli-{uuid.uuid4().hex[:8]}"
    sessions = list_sessions()
    if sessions:
        _print_sessions()
        choice = input("输入编号续聊，直接回车开启新会话：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            return sessions[int(choice) - 1].thread_id
    return f"cli-{uuid.uuid4().hex[:8]}"


def _print_answer(result: dict) -> None:
    messages = result.get("messages", [])
    answer = next(
        (m.content for m in reversed(messages) if m.type == "ai" and m.content),
        None,
    )
    print("\n小T：")
    print(answer or "（模型未返回文本，可换个问法再试）")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="小T 教材答疑助手")
    parser.add_argument("--thread", help="直接恢复指定 thread_id 的会话")
    parser.add_argument("--list", action="store_true", help="列出历史会话后退出")
    args = parser.parse_args()

    if args.list:
        _print_sessions()
        return

    graph = build_agent()
    thread_id = args.thread or _choose_starting_thread()
    print(_BANNER)
    print(f"当前会话：{thread_id}\n")

    try:
        while True:
            try:
                question = input("你：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if not question:
                continue
            if question in {"/exit", "/quit"}:
                print("再见！")
                break
            if question == "/new":
                thread_id = f"cli-{uuid.uuid4().hex[:8]}"
                print(f"（已开启新会话 {thread_id}）\n")
                continue
            if question == "/sessions":
                _print_sessions()
                continue
            if question.startswith("/resume"):
                parts = question.split(maxsplit=1)
                if len(parts) != 2:
                    print("用法：/resume <thread_id 或会话编号>")
                    continue
                target = parts[1].strip()
                if target.isdigit():
                    sessions = list_sessions()
                    idx = int(target)
                    if not 1 <= idx <= len(sessions):
                        print("编号超出范围，用 /sessions 查看。")
                        continue
                    thread_id = sessions[idx - 1].thread_id
                else:
                    thread_id = target
                print(f"（已切换到会话 {thread_id}，历史上下文已恢复）\n")
                continue

            # tags 会带进 LangSmith trace，可在平台按入口（cli/web）过滤；
            # thread_id 由 LangGraph 自动写入 trace metadata，无需手动注入
            config = {
                "configurable": {"thread_id": thread_id},
                "tags": ["teach-agent", "cli"],
            }
            try:
                result = graph.invoke(
                    {"messages": [HumanMessage(content=question)]}, config
                )
            except Exception as exc:  # 网络/限流等不应中断整个 REPL
                print(f"\n[调用失败] {exc}\n")
                continue
            _print_answer(result)
    finally:
        # 进程退出前等后台 trace 队列发完，避免最后一条对话丢 trace
        wait_for_all_tracers()


if __name__ == "__main__":
    sys.exit(main())
