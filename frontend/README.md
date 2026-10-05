# teach_agent Web 前端

小T教材答疑的 Web 界面：Vite + React + TypeScript。

- 与后端通过 `/api` 交互（开发态由 Vite 代理到 `http://127.0.0.1:8000`，见 `vite.config.ts`）
- SSE 流式问答（`POST /api/chat/stream`，fetch reader 逐事件解析，非 EventSource）
- Markdown + KaTeX 渲染（react-markdown / remark-gfm / remark-math / rehype-katex）
- 工具调用过程可展开；search_book 结果解析为来源页码卡片
- 书架上传三态轮询、历史会话回放

## 命令

```bash
npm install
npm run dev      # 开发服务器 http://localhost:5173（需同时启动后端 uvicorn :8000）
npm run build    # 类型检查 + 产物输出到 dist/，可由 FastAPI 单端口托管
npm run preview
```

接口契约与事件类型见 `src/types.ts`、`src/api.ts`；后端说明见根目录 [README.md](../README.md) 与 [docs/USAGE.md](../docs/USAGE.md)。
