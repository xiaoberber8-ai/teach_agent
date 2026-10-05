# 小T · teach_agent

**只依据你本地教材原文作答、每条结论标注书名页码的中文教材答疑 Agent。**

基于 [DeepAgents](https://github.com/langchain-ai/deepagents)（LangGraph 之上的 Agent harness）构建：本地 RAG 负责"取证"，LLM 负责"教学"，五条教学法 Skill 负责"怎么教"，CLI 与 Web（FastAPI + React）双入口共享同一套 Agent 装配与会话记忆。

本项目也是 Datawhale《Deep Agents 实战》课程（ch02–ch09）的**综合实战作品**：课程中每个核心机制——工具调用循环、虚拟文件系统 jail、Middleware、Skills 渐进披露、Checkpointer 短期记忆 / Store 长期记忆——都在本项目中有意识地落地或取舍，详见下文 [DeepAgents 课程知识在本项目的落地](#deepagents-课程知识在本项目的落地)。

![Web 端概念答疑：侧栏历史会话 + 带页码引用的流式回答](docs/images/web-answer-citations.png)

---

## 它能做什么

- **教材语义问答**：问概念、问公式推导、问题目、让它带你复习一章；答案只来自已入库教材原文，关键结论逐处标注《书名》章节与页码，末尾附完整引用清单。
- **低分不硬答**：向量相似度低于阈值即进入拒答流程，明说"这套教材里没有直接讲到"，不用模型参数记忆编造书本外结论。
- **教学法而非聊天**：内置 5 条可执行的教学 Skill（学习教练 / 概念讲解 / 公式推导 / 习题辅导 / 章节复习），覆盖最近发展区定级、提取练习 + 间隔 + 交错练习、苏格拉底式错题引导，而不是直接甩答案。
- **多轮记忆**：基于 LangGraph checkpointer（SQLite），同一 `thread_id` 跨进程续聊，"那它的公式呢？"这类指代会被正确解析。
- **两种用法**：终端 REPL（CLI）与浏览器 Web（SSE 逐 token 流式、工具调用过程可展开、来源页码卡片、书架上传三态、历史会话回放）。
- **全本地取证**：embedding 使用本地 Qwen3-Embedding-0.6B，教材文本不出本机；网络检索（Tavily）与教材主链路隔离，仅在用户明确要求书外拓展时启用。

## 快速开始

### 1. 环境要求

- Python ≥ 3.12、[uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20（仅构建/开发 Web 前端时需要）
- 首次运行会从 HuggingFace 缓存加载本地 embedding 模型（Qwen3-Embedding-0.6B）；有 GPU 自动使用 CUDA，无 GPU 走 CPU

### 2. 安装与配置

```bash
git clone https://github.com/xiaoberber8-ai/teach_agent.git
cd teach_agent
uv sync                       # 安装 Python 依赖
cp .env.example .env          # 然后填入你自己的 TEACH_LLM_API_KEY
```

`.env` 中只需配置答疑 LLM（任何 OpenAI 兼容端点均可，默认 DeepSeek）：

```dotenv
TEACH_LLM_BASE_URL=https://api.deepseek.com/v1
TEACH_LLM_API_KEY=sk-xxxx          # 填你自己的 key，.env 已被 .gitignore 忽略
TEACH_LLM_MODEL=deepseek-flash
```

> M1 入库与检索链路完全离线，不需要任何 API key；只有 M2 起的 Agent 问答需要 LLM key。Tavily、LangSmith 均为可选项。

### 3. 入库一本教材

```bash
uv run python -m teach_agent.ingest /path/to/机器学习.pdf --title "机器学习（周志华）"
uv run python -m teach_agent.list          # 查看书架与入库状态
```

文字版 PDF / TXT 均可；扫描版 PDF（无文字层）会被准确识别并拒绝入库并给出提示（OCR 在路线图 M6）。

### 4. 开始提问

**命令行：**

```bash
uv run python -m teach_agent.chat          # 交互式 REPL
uv run python -m teach_agent.chat --list   # 列出历史会话
```

**Web（开发模式）：**

```bash
uv run uvicorn teach_agent.server:app --host 127.0.0.1 --port 8000   # 终端 A
cd frontend && npm install && npm run dev                            # 终端 B（:5173，/api 代理到 8000）
```

**Web（单端口生产模式）：**

```bash
cd frontend && npm install && npm run build      # 产物落 frontend/dist
cd .. && uv run uvicorn teach_agent.server:app --host 127.0.0.1 --port 8000
# 浏览器直接打开 http://127.0.0.1:8000，FastAPI 托管构建产物并对前端路由做 SPA 回退
```

更详细的功能说明、接口与排障见 **[docs/USAGE.md](docs/USAGE.md)**。

## 系统架构

```mermaid
flowchart LR
    subgraph 入口
        CLI["CLI REPL<br/>chat.py（同步图）"]
        WEB["Web UI<br/>React + SSE"]
    end
    WEB -->|POST /api/chat/stream| API["FastAPI<br/>astream_events v2（异步图）"]

    subgraph DeepAgents harness
        GRAPH["create_deep_agent<br/>ReAct 循环 + 系统提示词"]
        SKILL["SkillsMiddleware<br/>渐进披露：系统提示只列名/描述<br/>read_file 按需读 SKILL.md"]
        GRAPH --- SKILL
        FS["FilesystemBackend<br/>virtual jail: data/agent_fs<br/>仅保留只读 read_file"]
        GRAPH --- FS
    end

    CLI --> GRAPH
    API --> GRAPH

    GRAPH -->|"list_books / search_book / read_chunk"| RAG["本地 RAG 工具层"]
    GRAPH -->|"web_search（隔离，仅书外拓展）"| NET["Tavily"]

    subgraph RAG 数据面
        RAG --> CHROMA[("Chroma<br/>cosine")]
        RAG --> EMB["Qwen3-Embedding-0.6B<br/>query 指令前缀 / last-token pooling"]
        INGEST["ingest 流水线"] --> PDF["PyMuPDF 解析<br/>章节识别 + 页眉清洗"]
        PDF --> SPLIT["章节感知切块<br/>700 字 / 重叠 80"]
        SPLIT --> EMB
        EMB --> CHROMA
    end

    GRAPH <--> CKPT[("SQLite checkpointer<br/>CLI/异步图共享同一文件")]
```

### DeepAgents 装配要点（`src/teach_agent/agent.py`）

- `create_deep_agent()` 统一装配同步 / 异步两张图：同一模型、同一工具集、同一系统提示词、同一 jail 与 Skill 目录，差异仅在 checkpointer。
- 通过 `register_harness_profile()` 为本模型注册 harness 策略：**关闭 general-purpose 子代理**（`task` 工具），并剔除 7 个写/执行类内置文件工具（`write_file/edit_file/delete/ls/glob/grep/execute`），只保留只读 `read_file`——因为 SkillsMiddleware 的渐进披露要求模型自行读取 `SKILL.md`，而模型不应拥有任何写文件或执行命令的能力。
- `FilesystemBackend(root_dir=data/agent_fs, virtual_mode=True)` 构成文件系统沙箱（jail）：模型所见路径全部为虚拟路径，`../` 逃逸会被直接抛错阻断；产品 Skill 源文件在仓库 `skills/`，Agent 启动时增量同步进 jail。
- 持久化：CLI 用同步 `SqliteSaver`，Web 用 `AsyncSqliteSaver`（`astream_events` 必须异步 checkpointer），两者经 WAL 模式共享同一个 `data/checkpoints.sqlite`。

### RAG 流水线（M1）

| 环节 | 实现 | 关键处理 |
|---|---|---|
| 解析 | PyMuPDF | NBSP 规范化、页眉/页脚碎片剔除；平均每页字符数 < 50 判定为扫描版并拒绝 |
| 切块 | 章节感知切分（`splitting.py`） | 以目录点引为高信任源收割章节名，700 字/块、80 字重叠，块元数据带书名、章节、起止页码 |
| 向量化 | Qwen3-Embedding-0.6B（本地） | query 侧拼 `Instruct: {task}\nQuery:` 指令、last-token pooling、L2 归一；文档侧不加指令 |
| 检索 | Chroma cosine | TOP_K=6，distance 阈值 0.42（≈ 相似度 0.58，保守初值） |

### 工具集：只"取证"，不"作答"

`list_books` / `search_book` / `read_chunk` / `web_search`（见 `tools.py`）全部只返回原文证据与结构化的"未检索到"原因；所有结论由 LLM 基于证据组织。`search_book` 的 docstring 明确引导模型换同义词/英文术语重试 1-3 次后再拒答，`read_chunk` 支持 `include_adjacent` 取回跨页证明的完整上下文。

### 教学 Skill（`skills/*/SKILL.md`）

| Skill | 触发场景 | 核心方法 |
|---|---|---|
| learning-coach | 目标/节奏类、首次对话 | 一次只问一个澄清问题；最近发展区三级定级；流畅度 ≠ 存储强度；提取练习 + 间隔（今天/明天/三天后）+ 交错；**其他四技能的调度入口** |
| concept-explainer | "X 是什么" | 一句话直答 → 类比（含失效点）→ 教材定义 → 正反例 → 易混对比表 → 闭卷自测 |
| formula-derivation | 公式推导 | 符号清单 → 逐步标注依据 → 量纲/特例检验 → 闭卷变式口算；含 OCR 错符还原约定 |
| exercise-tutor | 做题/对答案 | 错因四级分类、提示四级阶梯，禁止直接给答案，作答后即时反馈 |
| chapter-review | 章末复习 | 知识地图 → 结论卡 → 三类考点 → 5–9 道交错盲测题；禁止补充检索不到的小节 |

系统提示词（`prompts.py`）中只写入技能路由规则与名称，`SKILL.md` 全文由模型在需要时自行 `read_file` 拉取——这是 DeepAgents SkillsMiddleware 的**渐进披露（progressive disclosure）**模式：控制每轮上下文体积，同时让技能内容可以独立迭代、随仓库分发。

## DeepAgents 课程知识在本项目的落地

> 对应 Datawhale《Deep Agents 实战》ch02–ch09。下面不是课程摘抄，而是每个机制在本项目代码中的**具体落点与取舍理由**。

### 1. 三层心智模型：Runtime → Framework → Harness

| 层 | 角色 | 本项目对应 |
|---|---|---|
| **Runtime：LangGraph** | 状态图运行时——状态通道、超步（superstep）循环、checkpointer、中断恢复、流式事件 | 编译产物是一个 `CompiledStateGraph`（继承链 Pregel → Runnable），`model ⇄ tools` 成环、条件边决定回到模型还是 END |
| **Framework：LangChain** | 模型/工具的标准化接口（ChatModel、Tool、消息类型、Runnable 协议） | `ChatOpenAI` 走 OpenAI 兼容协议接 DeepSeek；`@tool` 把函数签名翻译成 JSON Schema |
| **Harness：Deep Agents** | 在 Runtime/Framework 之上预装"Agent 工作套件"：文件系统、子代理、Skills、摘要、规划——全部以 Middleware 形式挂载 | `create_deep_agent()` 一行装配；harness profile 裁剪工具面 |

关键认知：**模型是冻结的文本函数，工具是它连接真实世界的唯一通道**。"工具调用"是四方协作——`create_deep_agent` 注册 schema → 模型只输出 `tool_calls`（点菜）→ LangGraph 在本机执行 Python 函数（上菜）→ 结果包成 ToolMessage 回灌 → 模型再推理。模型永远看不到函数体，所以工具的 **docstring 不是注释而是模型的说明书**：本项目 4 个工具的 docstring 都按"做什么 / 何时调用 / 参数含义 / 空结果怎么办"写满（见 `tools.py`），这是模型能否正确取证的决定性因素。

### 2. 课程能力地图（ch02–ch09 → 本项目）

| 课程章节 | 核心机制 | 在本项目的落地 / 取舍 |
|---|---|---|
| ch02 第一个 Agent | `create_deep_agent(model, tools, system_prompt)`；`invoke` 的 model⇄tools 循环；OpenAI 兼容 `base_url` 切平台 | [agent.py](src/teach_agent/agent.py) `_assemble()`；换模型只改 `.env` 三项；CLI 用同步图，Web 用异步图 + `astream_events(version="v2")` 驱动 SSE |
| ch03 虚拟文件系统 | 虚拟文件 = state 中的 `files` 字典；Backend 决定物理存哪；`FilesystemBackend(virtual_mode=True)` 路径沙箱；大结果自动卸载（>2 万 token）/ 历史自动摘要（窗口 85%） | jail 落 `data/agent_fs/` 且开启 `virtual_mode`；剔除写/执行工具只留只读 `read_file`；工具输出 4000 字符截断 + `read_chunk` 按需复读，与 harness 自带的卸载/摘要共同做 Context Engineering |
| ch04 任务规划与 Middleware | 六 Hook 洋葱圈（`before/after_agent/model` + `wrap_model_call/tool_call`）；v0.7 起 `TodoListMiddleware` 需显式启用 | **刻意不启用 todo 规划**：教材答疑是"检索→取证→讲清"的短环任务，按课程决策表"单步问答、短工具调用保持关闭，避免计划比任务还长"；教学流程由系统提示词工作流 + Skill 剧本承担 |
| ch05 子 Agent 与上下文隔离 | 主 Agent 经 `task` 工具委派，子 Agent 在独立上下文运行、只回传摘要 | **经 harness profile 关闭通用子代理**（见下节）：答疑必须维持单一教师人格与统一引用规范，委派会稀释上下文且扩大工具面 |
| ch06 异步子 Agent | `AsyncSubAgent` 派活即返回任务 ID，五个遥控器工具（start/check/update/cancel/list） | 未采用。注意区分三种"异步"：本项目 Web 的异步是**编程层 `async/await` + 异步 checkpointer + SSE**，不是后台子任务编排；未来批量入库/长综述可考虑 |
| ch07 Skills 能力包 | Progressive Disclosure 三级；`SKILL.md` frontmatter 规范；Filesystem/State/Store 三种 Backend | 5 个教学技能随仓库分发，启动时增量同步进 jail；系统提示只挂 name+description，模型按需 `read_file` 读全文（详见第 4 节） |
| ch08 长期记忆 | **短期记忆 = Checkpointer/thread，长期记忆 = Store/namespace**；`CompositeBackend` 按路径前缀路由（如 `/memories/` → StoreBackend） | M2 已落地 SQLite checkpointer（CLI/异步图共享同一文件）；**M4 将引入 SqliteStore + namespace（按学生隔离）承载跨会话学习画像**，让 learning-coach 的定级与间隔复习持久化 |
| ch09 Human-in-the-Loop | `interrupt_on` 风险分级暂停，`Command(resume=...)` 凭同一 `thread_id` 恢复（必须配 Checkpointer，中断逻辑放 Node-style 钩子） | 暂未启用：工具面全部只读、无破坏性动作。未来"删除教材/重建索引"等敏感操作可加审批节点 |

### 3. Agent 循环与流式：从 invoke 到 SSE

CLI 走 `agent.invoke({"messages": [...]}, config={"configurable": {"thread_id": ...}})`，内部时间线：请求穿过 Middleware 洋葱圈（filesystem → subagents → summarization → todo → prompt_caching，最内层才是模型 HTTP 调用）→ AIMessage 带 `tool_calls` 则执行工具并回灌、继续循环，无 `tool_calls` 则到 END；递归上限防止死循环（超限 `GraphRecursionError`）。

Web 不能用 `invoke`——前端要逐 token 显示并展示工具过程。因此：

- 另装一张**异步图**（`build_async_agent()`）：模型、工具、提示词、jail、skills 与同步图完全一致，仅把 `SqliteSaver` 换成 `AsyncSqliteSaver`（`astream_events` 不支持同步 saver），两者经 WAL 模式共享同一个 `data/checkpoints.sqlite`；
- `graph.astream_events(version="v2")` 把 `on_chat_model_stream` 映射为 SSE `token` 事件，`on_tool_start/end` 映射为 `tool_start/tool_end`，收尾发 `done`/`error`；
- 前端用 fetch reader 手工解析 SSE（非 EventSource——需要 POST 发消息体）。

### 4. Harness 安全裁剪：为什么关掉子代理、只留 read_file

课程默认 harness 给 Agent 配齐 8 个文件工具与一个可无限委派的通用子代理；那是为"通用编程助手"设计的。教材答疑的攻击面和能力需求都不同，本项目通过 `register_harness_profile()` 为本模型注册定制 profile：

```python
profile = HarnessProfile(
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    excluded_tools=frozenset({"ls","write_file","edit_file","delete","glob","grep","execute"}),
)
register_harness_profile(f"openai:{config.LLM_MODEL}", profile)
```

- **保留 `read_file`**：SkillsMiddleware 的渐进披露要求模型自己读取 `/skills/<name>/SKILL.md`，这是唯一需要的文件能力；
- **剔除其余 7 个**：写/改/删/执行对答疑无用，且 `LocalShellBackend` 式的 `execute` 无沙箱；
- **关闭通用子代理**：`task` 委派会把问题丢给另一个人格的上下文，无法保证引用格式、拒答规则与教学节奏一致——隔离是 ch05 中子代理的优点，却是本场景的缺点；
- **jail 双保险**：`FilesystemBackend(root_dir=data/agent_fs, virtual_mode=True)`，模型所见全为虚拟路径，`../../../etc/passwd` 类逃逸直接抛 `ValueError` 阻断（实测验证）。技能源在仓库 `skills/`，`_sync_skills_into_jail()` 仅在内容变化时增量复制。

### 5. Skills：把教学法做成可复用能力包

课程第 7 章的三级渐进披露在本项目被严格执行：

| 级别 | 加载内容 | 时机 | 本项目 |
|---|---|---|---|
| L1 Metadata | `name` + `description` | 启动时全量 | 系统提示词只出现 5 条技能的路由规则，正文一个字不进常驻上下文 |
| L2 Instructions | `SKILL.md` 全文 | 模型判定匹配后 | trace 中可见模型主动 `read_file('/skills/concept-explainer/SKILL.md', limit=1000)` |
| L3 Resources | 引用文件 | 正文需要时 | 预留 `references/` 扩展位 |

frontmatter 合规性按课程规范执行：`name` 小写连字符且**与父目录同名**（≤64 字符），`description` ≤1024 字符且写成"触发场景 + 做什么 + 产出什么"的召回句式——因为 description 是技能被选中的**唯一依据**，写模糊了就会漏召回/误召回。

**Skills / Memory / Tools 的分工**（课程第 8 节的决策规则）在本项目的应用：

- **Tools**：原子取证动作（检索、读片段）——每轮都需要 → `tools.py`
- **Skills**：特定任务才需要的长篇教学剧本（5 种课型）——按需加载 → `skills/`
- **Memory**：所有对话始终相关的全局规范（引用格式、拒答红线、人格）——启动即加载 → `prompts.py` 的系统提示词；M4 之后学生个人画像将成为第二层 Memory（Store 中按 namespace 持久的 `/memories/AGENTS.md` 模式）

### 6. 记忆分层：thread 存档与跨会话画像

课程里"游戏存档 vs 游戏背包"的比喻直接指导了本项目设计：

- **存档（Checkpointer，已落地）**：每个超步后消息历史、工具轨迹序列化进 SQLite；同一 `thread_id` 跨进程完整续聊，多轮指代（"它的公式呢"）因此可解析；换 thread 即全新隔离。Web 的历史会话列表与回放接口（`sessions.py` + `GET /api/sessions/{id}/messages`）读的也是它。
- **背包（Store，M4 路线）**：`SqliteStore` 按 `namespace`（如学生 ID）跨 thread 共享，配合 `CompositeBackend` 把 `/memories/` 前缀路由到 StoreBackend——Agent 仍用普通的 `read_file/edit_file` 读写，框架自动决定"下班清桌还是永久归档"。学习画像（ZPD 定级、错题档案、间隔复习到期点）将存于此。

## 目录结构

```
teach_agent/
├── src/teach_agent/
│   ├── agent.py          # DeepAgents 装配：harness profile / jail / skills / 双 checkpointer
│   ├── prompts.py        # 系统提示词：工作流、引用与拒答规则、技能路由
│   ├── tools.py          # 4 个 RAG/网络工具（只取证）
│   ├── parsers.py        # PDF/TXT 解析与扫描版检测
│   ├── splitting.py      # 章节感知切块
│   ├── embeddings.py     # Qwen3 embedding 单例（双重检查锁）
│   ├── vectorstore.py    # Chroma collection
│   ├── retriever.py      # 阈值检索
│   ├── store.py/schema.py# 书架元数据 SQLite（books 表三态）
│   ├── indexing.py       # 入库流水线
│   ├── ingest.py/list.py/search.py  # M1 CLI
│   ├── chat.py           # M2 多轮 REPL
│   ├── sessions.py       # 跨进程列出 checkpoint 会话
│   └── server.py         # M3 FastAPI：书架/上传/SSE 问答/会话回放
├── skills/               # 5 条教学技能（随仓库分发，启动时同步入 jail）
├── frontend/             # Vite + React + TypeScript（SSE 流式 / KaTeX / 来源卡片）
├── scripts/              # 样例 PDF 生成脚本
├── docs/                 # 使用说明与截图
├── .env.example
└── pyproject.toml
```

运行时产物全部落在 `data/`（原书、切块、Chroma、两个 SQLite、jail），已在 `.gitignore` 中忽略，整目录删除即可完全重置。

## 可观测性

设置 `LANGSMITH_TRACING=true` 与 `LANGSMITH_API_KEY` 后，ReAct 每一步的 model/tools 耗时、token、工具入参出参都会上报到 LangSmith，可直接观察"检索 → 读技能 → 复读原文 → 作答"的完整链路：

![LangSmith 中的一次概念答疑：list_books → search_book → read_file(concept-explainer) → read_chunk → 最终回答](docs/images/langsmith-concept-rag-skills.png)

## 路线图

- [x] **M1** 离线 RAG：入库流水线 + 章节切块 + 本地 embedding + 阈值检索
- [x] **M2** DeepAgents CLI：ReAct 工具调用、jail、教学 Skill、SQLite 持久会话
- [x] **M3** Web：FastAPI SSE 流式问答、书架上传三态、React 前端、历史会话回放
- [ ] **M4** 长期记忆（课程 ch08）：`SqliteStore` 按学生 namespace 持久化 + `CompositeBackend` 把 `/memories/` 路由到 StoreBackend，承载跨会话的水平定级、错题档案与间隔复习调度（让 learning-coach 从会话内升级为持久画像）
- [ ] **M5** BM25 + 向量混合检索（改善符号/定理名等关键词场景）
- [ ] **M6** OCR 入库（marker / 视觉模型，支持扫描版教材）

## 安全说明

- 仓库不包含任何 API key：`.env` 已被 git 忽略，提交前请确认不要使用 `git add -f` 强制加入。
- Agent 的文件访问被 jail 限制在 `data/agent_fs/` 内且只读；写/执行类工具在 harness 层被剔除。
- 教材原文与向量库均保留在本机，答疑检索不经过外部服务；只有 LLM 推理与（显式触发的）网络检索会发出网络请求。
