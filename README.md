# Agentic RAG Assistant

一个中文知识库问答助手：你把文档交给它，它**只根据这些文档**回答问题，并在答案里附上可追溯的引用（`viking://...`）。知识库里没有的问题，它会明确拒答，而不是编造。

> 这份 README 面向懂一点 AI 概念（知道"大模型""API"是什么）的初级开发者和产品经理。不需要预先了解 RAG、MCP 或 Agent，正文会边用边解释。

## 它解决什么问题

直接问大模型"我们 AURORA-LX-9001 的标配电池容量是多少"，它不可能答对——模型没见过你的内部资料，硬答就是**幻觉**（一本正经地编造）。

这个项目用 **RAG（Retrieval-Augmented Generation，检索增强生成）**解决：

```text
普通 AI 聊天：  你的问题 ──► 大模型 ────────────► 答案（可能编造）
本项目（RAG）： 你的问题 ──► 先检索知识库 ──► 大模型只依据检索结果作答 ──► 答案 + 引用
```

一句话总结：**先查资料、再回答、附出处、查不到就说不知道。**

## 两种使用方式

| 入口 | 适合谁 | 说明 |
|---|---|---|
| **REPL**（终端聊天） | 想马上试用的人 | 在命令行里像聊天一样提问 |
| **REST API** | 开发者 | HTTP 调用，答案以 SSE（服务器推送事件）流式返回 |

两种入口共享同一个 Agent Core（核心逻辑）和 SQLite 会话存储，行为一致：聊过的会话在服务重启后还能继续。

## 它是怎么工作的

```text
                     用户
            ┌────────────┴────────────┐
            ▼                         ▼
       REPL 终端                  REST API
            └────────────┬────────────┘
                         ▼
                Python Agent Core
                （同一个核心逻辑）
                   │           │
                   ▼           ▼
          Claude Agent SDK   OpenViking 命令桥
          （模型循环、工具）  （SDK 优先 + REST 兜底）
                   │           │
                   ▼           ▼
          DeepSeek 大模型    OpenViking Server
          （生成答案）       （存储与检索知识库）
```

三个外部角色，用图书馆打比方：

| 组件 | 作用 | 类比 |
|---|---|---|
| OpenViking Server | 独立的知识存储与检索服务 | 图书馆：书架 + 检索系统 |
| DeepSeek API | 生成答案的大模型 | 拿着资料写总结的人 |
| Claude Agent SDK | 决定"什么时候检索、怎么调工具"的编排层 | 带着问题去图书馆的助理 |

几个关键设计：

- **OpenViking Server 是独立进程**，本服务只连接它、不管理它，所以要先启动它（见快速开始第 2 步）。
- 问答走 OpenViking 原生 `/mcp` 端点（MCP = Model Context Protocol，模型调用外部工具的标准协议），不额外自建 MCP server。
- Claude Agent SDK 负责模型循环、工具调用、会话存储接入和 runtime 子进程生命周期——本项目不是简单包一层 DeepSeek API。
- REST 默认只监听 `127.0.0.1`（本机）；密钥只从本地 `.env` 读取，调用方无法传入供应商密钥。

## 快速开始

### 准备

- Windows + 本仓库（自带配好依赖的 `.venv` 虚拟环境，Python 3.11）
- 一个 [DeepSeek API Key](https://platform.deepseek.com)（真实问答必需）
- [Ollama](https://ollama.com) 已安装，并拉取检索用的 embedding 模型：

```powershell
ollama pull qwen3-embedding:0.6b
```

### 第 1 步：配置 `.env`

复制 `.env.example` 为 `.env`，填入你的 DeepSeek Key：

```dotenv
DEEPSEEK_API_KEY=你的密钥
```

其余变量保持默认即可。**不要把 `.env` 和真实密钥提交到仓库。**

### 第 2 步：启动 OpenViking Server（新开一个终端）

```powershell
cd D:\project\Harness\python-claude-sdk

.\.venv\Scripts\openviking-server.exe `
  --config=D:\project\Harness\python-claude-sdk\agentic-rag-ov.conf `
  --host=127.0.0.1 `
  --port=1933 `
  --workers=1
```

看到 `OpenViking HTTP Server is running on 127.0.0.1:1933` 就成功了。这个终端要保持开着。

### 第 3 步：启动本服务（再开一个终端）

```powershell
# REST API 方式（默认）
.\.venv\Scripts\python.exe -m agentic_rag serve

# 或者：REPL 终端聊天方式
.\.venv\Scripts\python.exe -m agentic_rag repl
```

所有命令都必须使用仓库虚拟环境中的 Python（如上所示）。REPL 支持 `/new`（新会话）、`/session <session-id>`（切换会话）、`/current`（查看当前会话）、`/clear`（清屏）、`/exit`（退出）。

### 第 4 步：上传示例文档

示例文档在 `samples/` 目录。先建立隔离的示例知识库目录，再上传文档（curl 示例假设服务在 `127.0.0.1:8000`；上传另外两份文档时替换文件名即可）：

```bash
# 建立隔离的示例知识库目录
curl -sS -X POST http://127.0.0.1:8000/commands/execute \
  -H 'Content-Type: application/json' \
  --data '{"command":"mkdir","arguments":{"uri":"viking://resources/agentic-rag-demo"}}'

# 上传第一份示例文档
curl -sS -X POST http://127.0.0.1:8000/commands/execute \
  -F 'command=add-resource' \
  -F 'arguments_json={"parent":"viking://resources/agentic-rag-demo"}' \
  -F 'file=@samples/01-product-facts.md;type=text/markdown'
```

> Windows PowerShell 用户：请用 `curl.exe` 代替 `curl`（`curl` 在 PowerShell 里是 `Invoke-WebRequest` 的别名）。示例中的单引号写法在 PowerShell 中可直接使用。

### 第 5 步：提问

REST 方式（答案以 SSE 事件流返回，其中 `text_delta` 是答案文本，`citation` 是引用，`done` 表示结束并汇总引用）：

```bash
curl -N -X POST http://127.0.0.1:8000/qa/stream \
  -H 'Content-Type: application/json' \
  --data '{"message":"AURORA-LX-9001 的标配电池容量是多少？","target_uri":"viking://resources/agentic-rag-demo"}'
```

REPL 方式：启动后直接输入问题即可。

## 验证效果：三个必试问题

示例知识库有三份中文文档，固定上传到隔离的 Demo 命名空间 `viking://resources/agentic-rag-demo`，不会污染其他数据：

| 文件 | 内容 |
|---|---|
| `samples/01-product-facts.md` | AURORA-LX-9001 产品事实与适用范围 |
| `samples/02-operations-runbook.md` | 运维手册（索引并发、熔断、一致性检查等） |
| `samples/03-research-conclusions.md` | 调研结论与显式适用限制 |

依次问这三个问题，可以完整看到"检索、综合、拒答"三种行为：

1. **单文档事实检索**：`AURORA-LX-9001 的标配电池容量是多少？` → 应给出带引用的准确答案
2. **跨文档综合**：`结合产品、运维和研究三份文档，AURORA-LX-9001 的电池容量、索引并发上限与调研适用边界是什么？` → 应综合三处来源
3. **应该拒答**：`AGENTIC-RAG-DEMO-NO-SUCH-CLAIM-9931 是什么？` → 应明确拒绝，而不是编造

有依据的回答必须给出 OpenViking `viking://` 引用；知识库不支持的问题应明确拒绝，不得为"缺失本身"伪造引用。

## REST API 速览

应用自身端点：

| 方法与路径 | 用途 |
|---|---|
| `GET /health` | 健康检查；OpenViking 未启动时返回 503 |
| `GET /commands` | 返回完整 CLI Semantic Command 清单 |
| `GET /sessions` | 列出持久化会话 |
| `GET /sessions/{session_id}` | 读取会话摘要与记录 |
| `DELETE /sessions/{session_id}` | 删除指定会话 |
| `POST /qa/stream` | SSE 流式问答 |
| `POST /commands/execute` | JSON 或 multipart 通用命令执行 |
| `GET /jobs/{job_id}` | 查询长任务状态 |

FastAPI 还自动提供交互式 API 文档：浏览器打开 `http://127.0.0.1:8000/docs`（Swagger UI）或 `/redoc` 即可试用。

### SSE 事件契约

SSE 的 `event:` 名称与 payload `type` 总是相同。所有事件都包含：

| 公共字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | 与 SSE event 名称相同 |
| `request_id` | string | 本次请求标识 |
| `session_id` | string | 稳定会话 UUID；缺失时由应用生成 |
| `timestamp` | string | UTC ISO-8601 时间 |

| Event / `type` | 额外字段 | 说明 |
|---|---|---|
| `message_start` | 无 | 会话锁取得并开始处理 |
| `thinking_delta` | `text: string` | 模型思考增量，与答案分离 |
| `tool_call` | `tool_call_id: string`, `tool_name: string`, `arguments: object` | OpenViking 工具调用与脱敏参数摘要 |
| `tool_result` | `tool_call_id: string`, `tool_name: string`, `error: boolean`, `content: any` | 工具结果；不是答案文本 |
| `citation` | `citation: string` | 首次发现的规范化 `viking://` 引用 |
| `text_delta` | `text: string` | 答案文本增量；可能继续触发 citation |
| `done` | `citations: string[]` | 成功终态与去重后的聚合引用 |
| `error` | `code: string`, `message: string` | 终态错误；之后不会再补发 `done` |

## 环境变量

复制 `.env.example` 为 `.env` 后填写：

| 变量 | 必填 | 默认值 | 含义 |
|---|---|---|---|
| `HOST` | 否 | `127.0.0.1` | REST 绑定地址；默认只监听本机 |
| `PORT` | 否 | `8000` | REST 端口 |
| `OPENVIKING_BASE_URL` | 是 | `http://127.0.0.1:1933` | OpenViking Server 基础地址 |
| `OPENVIKING_API_KEY` | 视 Server 模式 | 空 | OpenViking `X-API-Key`；本地免认证模式可为空 |
| `DEEPSEEK_BASE_URL` | 是 | `https://api.deepseek.com/anthropic` | DeepSeek Anthropic 兼容接口 |
| `DEEPSEEK_API_KEY` | 真实问答必填 | 空 | 只放在进程环境或本地 `.env`，不写入日志 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-flash` | DeepSeek 模型名 |
| `SESSION_DATABASE_PATH` | 否 | `data/sessions.sqlite3` | 会话与任务 SQLite 路径 |

## 常用命令精选

`POST /commands/execute` 支持一百多条命令，完整目录见 [docs/commands.md](docs/commands.md)。新手最常用的：

| 命令 | 用途 | 风险 |
|---|---|---|
| `health` / `status` | 检查 OpenViking 状态 | 只读 |
| `mkdir` | 创建知识库目录 | 变更 |
| `add-resource` | 上传文档 | 变更 |
| `ls` / `tree` | 浏览知识库内容 | 只读 |
| `read` | 读取一份文档 | 只读 |
| `search` | 语义检索 | 只读 |
| `rm` | 删除资源 | **破坏性** |

## 安全须知

- REST 默认只绑定 `127.0.0.1`，不要随意改成 `0.0.0.0` 暴露到外网。
- API 密钥只放在本地 `.env` 或进程环境变量，不写入代码、日志或 git。
- `rm` 是破坏性命令。清理示例数据时，删除整个 `viking://resources/agentic-rag-demo` 即可，删除后应轮询返回的 job 确认 OpenViking 删除完成。
- 不要删除仓库中的 `data/` 目录来"清理示例"：SQLite 和默认 OpenViking 数据是运行时状态。

## 版本与依赖说明

最终验证时安装的是 `openviking` 0.4.19（其 Server 的 `/mcp` 端点动态提供 15 个 MCP 工具）与 `openviking-sdk` 0.1.10。OpenViking 在线文档可能落后于安装包；**遇到文档与实际不一致，以本机已安装包和正在运行的 Server/OpenAPI 为准**。

主要依赖（已装在 `.venv` 中，无需手动安装）：

| 依赖 | 验证版本 | 大白话用途 |
|---|---:|---|
| `openviking` | 0.4.19 | 外部知识库 Server 与 CLI |
| `openviking-sdk` | 0.1.10 | 在 Python 里操作 OpenViking |
| `claude-agent-sdk` | 0.2.152 | Agent 编排（模型循环、工具调用） |
| `mcp` | 1.30.0 | 连接 OpenViking 的 MCP 端点 |
| `fastapi` | 0.141.1 | REST 框架 |
| `uvicorn` | 0.52.4 | 运行本机 HTTP 服务 |
| `sse-starlette` | 3.4.11 | 把事件流编码成 SSE |
| `httpx` | 0.28.1 | 调用 OpenViking health 与 REST |
| `pydantic` | 2.13.5 | 配置与请求校验 |
| `python-dotenv` | 1.2.3 | 读取 `.env` |
| `python-multipart` | 0.0.32 | 解析文件上传 |

SQLite 使用 Python 标准库 `sqlite3`，不引入异步数据库驱动。

## 项目结构

```text
agentic_rag/   核心代码（Agent Core、REST、REPL、会话、命令桥）
samples/       三份中文示例文档
docs/          命令目录、架构决策记录（ADR）、验证文档
data/          运行时数据（会话 SQLite），不要删除
scripts/       命令清单生成脚本
tests/         测试
```

## 常见问题

**Q：必须先启动 OpenViking Server 吗？**
是的。本服务只做健康检查和连接，不负责启动或管理它；Server 没启动时 `GET /health` 会返回 503，问答会失败。

**Q：为什么有些问题助手不回答？**
这是设计目标：知识库没有依据就拒答，避免幻觉。请把相关文档上传到知识库后再问。

**Q：重启服务后，聊天记录还在吗？**
在。会话持久化在 `data/sessions.sqlite3`，REPL 和 REST 共用。

**Q：在线文档和本机行为对不上？**
OpenViking 迭代较快，在线资料可能落后。以本机已安装包、实际 OpenAPI 和 `/mcp` 工具清单为准。

## 延伸阅读

- [完整命令目录与长任务语义](docs/commands.md)
- [REST API 端到端验证记录](docs/postman-rest-api-verification.md)
- [架构决策记录（ADR）](docs/adr/)
- [OpenViking 官网](https://openviking.ai) · [Claude Agent SDK 文档](https://docs.anthropic.com/en/docs/claude-code/sdk) · [DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api)
