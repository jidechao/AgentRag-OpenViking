# Postman REST API 人工验证指南

本文描述如何用 Postman 验证 Agentic RAG REST API 的完整用户可见契约，包括健康检查、命令目录、统一命令执行、multipart 上传、job 状态、SSE 问答、会话管理、错误契约、服务重启持久化，以及 FastAPI 自动生成的文档端点。

> 密钥规则：Postman 请求中**不需要也不应该**包含 `DEEPSEEK_API_KEY` 或 `OPENVIKING_API_KEY`。模型与 OpenViking 凭证只由服务进程从本地 `.env` 读取。

## 目录

1. [启动前提](#1-启动前提)
2. [Postman Environment](#2-postman-environment)
3. [端点总览](#3-端点总览)
4. [验证 FastAPI 文档端点](#4-验证-fastapi-文档端点)
5. [验证健康与命令目录](#5-验证健康与命令目录)
6. [验证统一命令执行](#6-验证统一命令执行)
7. [验证 multipart 上传与 job](#7-验证-multipart-上传与-job)
8. [验证 SSE 问答](#8-验证-sse-问答)
9. [验证会话端点](#9-验证会话端点)
10. [验证 REST 重启持久化](#10-验证-rest-重启持久化)
11. [验证错误契约](#11-验证错误契约)
12. [推荐 Collection 顺序](#12-推荐-collection-顺序)
13. [最终验收清单](#13-最终验收清单)
14. [注意事项](#14-注意事项)

## 1. 启动前提

### 1.1 确认 Ollama

```powershell
ollama list
```

当前部署建议使用：

```text
qwen3-embedding:0.6b
```

如果不存在：

```powershell
ollama pull qwen3-embedding:0.6b
```

### 1.2 启动 OpenViking Server

```powershell
cd D:\project\Harness\python-claude-sdk

.\.venv\Scripts\openviking-server.exe `
  --config=D:\project\Harness\python-claude-sdk\agentic-rag-ov.conf `
  --host=127.0.0.1 `
  --port=1933 `
  --workers=1
```

成功标志：

```text
OpenViking HTTP Server is running on 127.0.0.1:1933
```

### 1.3 配置 `.env`

在仓库根目录创建或确认 `.env`：

```dotenv
HOST=127.0.0.1
PORT=8000
OPENVIKING_BASE_URL=http://127.0.0.1:1933
OPENVIKING_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic
# 下一行等号后只在本机填写真实值；本文不保存真实密钥
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
SESSION_DATABASE_PATH=D:/project/Harness/python-claude-sdk/data/sessions.sqlite3
```

当前 OpenViking 配置为本地 `auth_mode=dev`，所以 `OPENVIKING_API_KEY` 可留空。

### 1.4 启动 Agentic RAG REST

```powershell
cd D:\project\Harness\python-claude-sdk

.\.venv\Scripts\python.exe -m agentic_rag serve
```

成功标志：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

启动时出现 command description drift warning 属于已验证的非实质漂移；只要服务没有退出，不影响验证。

## 2. Postman Environment

创建 Environment，例如：

```text
Agentic RAG Local
```

添加变量：

| Variable | Initial Value | Current Value | 用途 |
|---|---|---|---|
| `baseUrl` | `http://127.0.0.1:8000` | `http://127.0.0.1:8000` | REST 基础地址 |
| `demoScope` | `viking://resources/agentic-rag-demo` | 同左 | 三份样例所在命名空间 |
| `postmanScope` | `viking://resources/agentic-rag-postman` | 同左 | 上传/清理专用隔离命名空间 |
| `productUri` |  |  | 产品事实文档 URI |
| `sessionId` |  |  | 主问答会话 ID |
| `jobId` |  |  | 上传 job ID |
| `cleanupJobId` |  |  | 清理 job ID |
| `disposableSessionId` |  |  | 用于删除验证的一次性会话 ID |

建议设置：

- Request timeout：至少 `300000` ms，SSE 与模型调用可能较慢。
- Proxy：确保 localhost 不走系统代理，或在 Proxy 配置中绕过 `127.0.0.1`。

## 3. 端点总览

### 应用业务端点

| 方法与路径 | 说明 | 预期状态 |
|---|---|---|
| `GET /health` | 应用与 OpenViking 依赖健康状态 | `200` 或 `503` |
| `GET /commands` | 完整 CLI Semantic Command manifest | `200` |
| `GET /sessions` | 会话列表 | `200` |
| `GET /sessions/{session_id}` | 会话详情与 transcript | `200` 或 `404` |
| `DELETE /sessions/{session_id}` | 删除会话 | `200` 或 `404` |
| `POST /qa/stream` | SSE 流式问答 | `200`，错误也通过 SSE 返回 |
| `POST /commands/execute` | JSON 或 multipart 通用命令执行 | `200` / `400` / `404` / `500` / `502` |
| `GET /jobs/{job_id}` | 查询本地 job 并向 OpenViking 对账 | `200` 或 `404` |

### FastAPI 自动文档端点

| 方法与路径 | 说明 |
|---|---|
| `GET /openapi.json` | OpenAPI schema |
| `GET /docs` | Swagger UI |
| `GET /redoc` | ReDoc |

Postman 可以直接导入：

```text
http://127.0.0.1:8000/openapi.json
```

但 SSE、multipart、会话连续性、重启持久化仍需要按本文手工验证。

## 4. 验证 FastAPI 文档端点

### 4.1 `GET /openapi.json`

Request：

```http
GET {{baseUrl}}/openapi.json
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const schema = pm.response.json();

pm.test("has openapi document", () => {
    pm.expect(schema).to.have.property("openapi");
    pm.expect(schema).to.have.property("paths");
});
```

通过标准：

- HTTP `200`
- 返回 OpenAPI JSON

### 4.2 `GET /docs`

```http
GET {{baseUrl}}/docs
```

通过标准：

- HTTP `200`
- 返回 Swagger UI HTML

### 4.3 `GET /redoc`

```http
GET {{baseUrl}}/redoc
```

通过标准：

- HTTP `200`
- 返回 ReDoc HTML

## 5. 验证健康与命令目录

### 5.1 `GET /health`

Request：

```http
GET {{baseUrl}}/health
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();

pm.test("application is ready", () => {
    pm.expect(body.status).to.eql("ok");
    pm.expect(body.application.status).to.eql("ready");
});

pm.test("OpenViking is up", () => {
    pm.expect(body.dependencies.openviking_server.status).to.eql("up");
    pm.expect(body.dependencies.openviking_server.reachable).to.be.true;
});
```

通过标准：

```json
{
  "status": "ok",
  "application": {
    "status": "ready"
  },
  "dependencies": {
    "openviking_server": {
      "status": "up",
      "reachable": true,
      "detail": null
    }
  }
}
```

### 5.2 `GET /commands`

Request：

```http
GET {{baseUrl}}/commands
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();
const names = body.commands.map(command => command.name);

pm.test("command catalog is generated", () => {
    pm.expect(body.source).to.eql("openviking-cli");
    pm.expect(body.commands).to.be.an("array");
});

pm.test("current catalog has 107 commands", () => {
    pm.expect(body.commands.length).to.eql(107);
});

pm.test("representative commands exist", () => {
    for (const name of [
        "health",
        "status",
        "find",
        "read",
        "add-resource",
        "tree",
        "task list",
        "admin delete-account"
    ]) {
        pm.expect(names).to.include(name);
    }
});
```

当前快照为 107 条命令。若 manifest 更新，数量可能变化；此时应以 `agentic_rag/command_manifest.json` 和 `GET /commands` 的一致性为准，再更新 Postman 断言。

不要用 Runner 逐个执行全部 107 个命令。其中包含删除、恢复、账户管理、快照恢复、导出备份等破坏性或特权命令。

## 6. 验证统一命令执行

所有命令执行都使用：

```http
POST {{baseUrl}}/commands/execute
Content-Type: application/json
```

### 6.1 OpenViking `status`

Body：

```json
{
  "command": "status",
  "arguments": {},
  "request_id": "postman-status"
}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();

pm.test("unified envelope is successful", () => {
    pm.expect(body.ok).to.be.true;
    pm.expect(body.command).to.eql("status");
    pm.expect(body.request_id).to.eql("postman-status");
    pm.expect(body.error).to.be.null;
});

pm.test("OpenViking status is healthy", () => {
    pm.expect(body.data.is_healthy).to.be.true;
});
```

### 6.2 OpenViking `health`

Body：

```json
{
  "command": "health",
  "arguments": {},
  "request_id": "postman-openviking-health"
}
```

通过标准：

- HTTP `200`
- `ok=true`
- 返回 OpenViking health 数据

### 6.3 `tree` 检查三份样例

Body：

```json
{
  "command": "tree",
  "arguments": {
    "uri": "{{demoScope}}",
    "level_limit": 2,
    "node_limit": 100
  },
  "request_id": "postman-tree"
}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();
const files = body.data.filter(item => item.isDir === false);

pm.test("demo namespace has three sample files", () => {
    pm.expect(files.length).to.eql(3);
});

const product = files.find(item => item.uri.includes("01-product-facts.md"));

pm.test("product file exists", () => {
    pm.expect(product).to.be.an("object");
    pm.collectionVariables.set("productUri", product.uri);
});
```

当前可手动兜底设置为：

```text
viking://resources/agentic-rag-demo/01-product-facts/01-product-facts.md
```

### 6.4 `read` 产品事实

Body：

```json
{
  "command": "read",
  "arguments": {
    "uri": "{{productUri}}"
  },
  "request_id": "postman-read-product"
}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();
const content = typeof body.data === "string"
    ? body.data
    : JSON.stringify(body.data);

pm.test("product fact is present", () => {
    pm.expect(content).to.include("68 Wh");
});
```

### 6.5 未知命令

Body：

```json
{
  "command": "postman-no-such-command",
  "arguments": {},
  "request_id": "postman-unknown-command"
}
```

Tests：

```javascript
pm.test("HTTP 400", () => pm.response.to.have.status(400));

const body = pm.response.json();

pm.test("unknown command error is structured", () => {
    pm.expect(body.ok).to.be.false;
    pm.expect(body.command).to.eql("postman-no-such-command");
    pm.expect(body.error.code).to.eql("UNKNOWN_COMMAND");
});
```

通过标准：

- HTTP `400`
- `error.code=UNKNOWN_COMMAND`
- 不暴露堆栈或密钥

## 7. 验证 multipart 上传与 job

上传使用隔离的 `postmanScope`，避免污染三份正式样例所在的 `demoScope`。

### 7.1 创建上传目录

Request：

```http
POST {{baseUrl}}/commands/execute
Content-Type: application/json
```

Body：

```json
{
  "command": "mkdir",
  "arguments": {
    "uri": "{{postmanScope}}"
  },
  "request_id": "postman-mkdir-upload-scope"
}
```

通过标准：

- HTTP `200`
- `ok=true`

### 7.2 multipart 上传

Request：

```http
POST {{baseUrl}}/commands/execute
```

Body 选择 `form-data`：

| Key | Type | Value |
|---|---|---|
| `command` | Text | `add-resource` |
| `arguments_json` | Text | `{"parent":"{{postmanScope}}"}` |
| `request_id` | Text | `postman-upload-product` |
| `file` | File | `samples/01-product-facts.md` |

不要手动设置 `Content-Type`。Postman 会自动生成 multipart boundary。

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();

pm.test("upload is accepted", () => {
    pm.expect(body.ok).to.be.true;
    pm.expect(body.command).to.eql("add-resource");
    pm.expect(body.data.job).to.be.an("object");
});

pm.collectionVariables.set(
    "jobId",
    body.data.job.job_id
);
```

### 7.3 查询上传 job

Request：

```http
GET {{baseUrl}}/jobs/{{jobId}}
```

重复 Send，直到进入终态。正常预期为 `succeeded`。

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();
const job = body.data;

pm.test("job id matches", () => {
    pm.expect(job.job_id).to.eql(pm.collectionVariables.get("jobId"));
});

pm.test("job reaches terminal success", () => {
    pm.expect(job.status).to.eql("succeeded");
    pm.expect(job.error).to.be.null;
});

pm.test("OpenViking native task id is retained", () => {
    pm.expect(job.openviking_task_id).to.be.a("string");
});
```

### 7.4 不存在的 job

Request：

```http
GET {{baseUrl}}/jobs/postman-no-such-job
```

Tests：

```javascript
pm.test("HTTP 404", () => pm.response.to.have.status(404));

const body = pm.response.json();

pm.test("job not found envelope", () => {
    pm.expect(body.ok).to.be.false;
    pm.expect(body.error.code).to.eql("JOB_NOT_FOUND");
});
```

### 7.5 清理 Postman 上传命名空间

Request：

```http
POST {{baseUrl}}/commands/execute
Content-Type: application/json
```

Body：

```json
{
  "command": "rm",
  "arguments": {
    "uri": "{{postmanScope}}",
    "recursive": true
  },
  "request_id": "postman-cleanup-upload-scope"
}
```

Tests：

```javascript
pm.test("cleanup command is accepted", () => {
    const body = pm.response.json();
    pm.expect(body.ok).to.be.true;

    const job = body.data.job;
    pm.collectionVariables.set("cleanupJobId", job.job_id);
});
```

然后轮询：

```http
GET {{baseUrl}}/jobs/{{cleanupJobId}}
```

直到 `succeeded`。该命令只清理 `postmanScope`，不要删除整个 `data/` 目录。

## 8. 验证 SSE 问答

### 8.1 第一轮跨文档问答

Request：

```http
POST {{baseUrl}}/qa/stream
Content-Type: application/json
Accept: text/event-stream
```

Body：

```json
{
  "message": "结合产品、运维和研究三份文档，AURORA-LX-9001 的电池容量、索引并发上限与调研适用边界是什么？请给出 viking:// 引用。",
  "target_uri": "{{demoScope}}"
}
```

不传 `session_id`，让应用生成 UUID。

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const text = pm.response.text();

pm.test("SSE contains required event types", () => {
    for (const eventName of [
        "message_start",
        "thinking_delta",
        "tool_call",
        "tool_result",
        "citation",
        "text_delta",
        "done"
    ]) {
        pm.expect(text).to.include(`event: ${eventName}`);
    }
});

pm.test("SSE contains viking citations", () => {
    pm.expect(text).to.include("viking://");
});

pm.test("answer contains battery fact", () => {
    pm.expect(text).to.include("68 Wh");
});

pm.test("answer contains index limit", () => {
    pm.expect(text).to.include("4");
});

const sessionMatch = text.match(/"session_id"\s*:\s*"([^"]+)"/);

pm.test("session id is captured", () => {
    pm.expect(sessionMatch).to.be.an("array");
    pm.collectionVariables.set("sessionId", sessionMatch[1]);
});
```

通过标准：

- SSE 事件名齐全：`message_start`、`thinking_delta`、`tool_call`、`tool_result`、`citation`、`text_delta`、`done`
- 答案包含 `68 Wh`
- 答案包含索引并发上限 `4`
- 答案说明调研限制：不能推广到外部用户、无因果证明、仅覆盖中文知识库任务
- 引用为 `viking://...`
- Postman 保存 `sessionId`

### 8.2 同 session 上下文追问

Request：

```http
POST {{baseUrl}}/qa/stream
Content-Type: application/json
Accept: text/event-stream
```

Body：

```json
{
  "session_id": "{{sessionId}}",
  "message": "它的标配电池容量是多少？请继续给出 viking:// 引用。",
  "target_uri": "{{demoScope}}"
}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const text = pm.response.text();

pm.test("same session id is used", () => {
    const expected = pm.collectionVariables.get("sessionId");
    pm.expect(text).to.include(expected);
});

pm.test("follow-up resolves product context", () => {
    pm.expect(text).to.include("AURORA-LX-9001");
    pm.expect(text).to.include("68 Wh");
});

pm.test("follow-up ends successfully", () => {
    pm.expect(text).to.include("event: done");
});
```

通过标准：

- 同一个 `session_id`
- 能理解“它”指 `AURORA-LX-9001`
- 答案包含 `68 Wh`
- 有 `viking://` 引用
- 以 `done` 结束

### 8.3 unsupported 问题拒答

Body：

```json
{
  "message": "AGENTIC-RAG-DEMO-NO-SUCH-CLAIM-9931 是什么？",
  "target_uri": "{{demoScope}}"
}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const text = pm.response.text();

pm.test("stream completes", () => {
    pm.expect(text).to.include("event: done");
});

pm.test("model refuses unsupported claim", () => {
    pm.expect(text).to.include("没有");
    pm.expect(text).to.include("无法给出");
});

const sessionMatch = text.match(/"session_id"\s*:\s*"([^"]+)"/);

if (sessionMatch) {
    pm.collectionVariables.set("disposableSessionId", sessionMatch[1]);
}
```

通过标准：

- 模型明确表示知识库中没有该标识
- 不编造事实
- 有真实检索活动
- 最终 `done`

## 9. 验证会话端点

### 9.1 `GET /sessions`

Request：

```http
GET {{baseUrl}}/sessions
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();

pm.test("session list is returned", () => {
    pm.expect(body.sessions).to.be.an("array");
});

pm.test("QA session is present", () => {
    const ids = body.sessions.map(item => item.session_id);
    pm.expect(ids).to.include(pm.collectionVariables.get("sessionId"));
});
```

### 9.2 `GET /sessions/{session_id}`

Request：

```http
GET {{baseUrl}}/sessions/{{sessionId}}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();

pm.test("session id matches", () => {
    pm.expect(body.session_id).to.eql(
        pm.collectionVariables.get("sessionId")
    );
});

pm.test("transcript entries are persisted", () => {
    pm.expect(body.entries).to.be.an("array");
    pm.expect(body.entries.length).to.be.above(0);
});
```

### 9.3 不存在的 session

Request：

```http
GET {{baseUrl}}/sessions/postman-no-such-session
```

Tests：

```javascript
pm.test("HTTP 404", () => pm.response.to.have.status(404));

const body = pm.response.json();

pm.test("session error is stable", () => {
    pm.expect(body.error.code).to.eql("SESSION_NOT_FOUND");
});
```

### 9.4 删除一次性会话

不要删除用于重启验证的 `sessionId`。使用 `disposableSessionId`。

Request：

```http
DELETE {{baseUrl}}/sessions/{{disposableSessionId}}
```

Tests：

```javascript
pm.test("HTTP 200", () => pm.response.to.have.status(200));

const body = pm.response.json();

pm.test("session is deleted", () => {
    pm.expect(body.deleted).to.be.true;
    pm.expect(body.session_id).to.eql(
        pm.collectionVariables.get("disposableSessionId")
    );
});
```

删除后再执行：

```http
GET {{baseUrl}}/sessions/{{disposableSessionId}}
```

预期 HTTP `404`。

## 10. 验证 REST 重启持久化

### 10.1 保留主会话 ID

确认 `sessionId` 已经保存，并且同 session 追问已成功。

### 10.2 重启 Agentic RAG REST

在 REST 服务终端按 `Ctrl+C` 停止服务，然后重新启动：

```powershell
cd D:\project\Harness\python-claude-sdk

.\.venv\Scripts\python.exe -m agentic_rag serve
```

OpenViking 与 Ollama 不需要重启。

### 10.3 重启后读取同一 session

Request：

```http
GET {{baseUrl}}/sessions/{{sessionId}}
```

通过标准：

- HTTP `200`
- `session_id` 不变
- `entries` 仍存在

### 10.4 重启后继续追问

Request：

```http
POST {{baseUrl}}/qa/stream
Content-Type: application/json
Accept: text/event-stream
```

Body：

```json
{
  "session_id": "{{sessionId}}",
  "message": "它的标配电池容量是多少？请继续给出 viking:// 引用。",
  "target_uri": "{{demoScope}}"
}
```

通过标准：

- 仍使用同一个 `session_id`
- 能回答 `AURORA-LX-9001`
- 能回答 `68 Wh`
- 有 `viking://` 引用
- 最终 `done`

## 11. 验证错误契约

### 11.1 空 QA message

Request：

```http
POST {{baseUrl}}/qa/stream
Content-Type: application/json
```

Body：

```json
{
  "message": ""
}
```

Tests：

```javascript
pm.test("SSE validation error uses HTTP 200", () => {
    pm.response.to.have.status(200);
});

const text = pm.response.text();

pm.test("error event is returned", () => {
    pm.expect(text).to.include("event: error");
    pm.expect(text).to.include("VALIDATION_ERROR");
});
```

通过标准：

- HTTP `200`
- SSE 返回 `event: error`
- payload `type=error`
- `code=VALIDATION_ERROR`
- 之后不会再补发 `done`

### 11.2 malformed command JSON

Request：

```http
POST {{baseUrl}}/commands/execute
Content-Type: application/json
```

Body 选择 raw，但内容故意写坏：

```text
{"command":}
```

通过标准：

- HTTP `400`
- unified Command Envelope
- `error.code=VALIDATION_ERROR`

## 12. 推荐 Collection 顺序

```text
Agentic RAG REST Verification
├── 00 Setup
│   ├── GET /health
│   └── GET /commands
├── 01 Commands
│   ├── POST status
│   ├── POST health
│   ├── POST tree
│   ├── POST read
│   └── POST unknown command
├── 02 Upload & Jobs
│   ├── POST mkdir postmanScope
│   ├── POST multipart add-resource
│   ├── GET job until succeeded
│   ├── GET missing job
│   ├── POST rm postmanScope
│   └── GET cleanup job until succeeded
├── 03 QA Stream
│   ├── grounded first turn
│   ├── context follow-up
│   └── unsupported refusal
├── 04 Sessions
│   ├── GET /sessions
│   ├── GET session
│   ├── GET missing session
│   ├── DELETE disposable session
│   └── GET deleted session
├── 05 Restart Persistence
│   ├── restart service manually
│   ├── GET same session
│   └── POST follow-up
├── 06 Validation
│   ├── empty QA message
│   └── malformed command JSON
└── 07 Docs
    ├── GET /openapi.json
    ├── GET /docs
    └── GET /redoc
```

## 13. 最终验收清单

| 验证项 | 通过标准 |
|---|---|
| Health | `status=ok`，应用 ready，OpenViking up |
| Command catalog | 当前 107 条命令，与 manifest 一致 |
| Status envelope | `ok=true`，OpenViking healthy |
| Tree | `demoScope` 下 3 个样例文件 |
| Read | 能读到 `68 Wh` |
| Multipart upload | 返回本地 job 并最终 succeeded |
| Native task ID | 上传 job 保留 `openviking_task_id` |
| Job status | succeeded 与 404 行为正确 |
| SSE grounded QA | 七类必需事件齐全 |
| Citation | 最终有 `viking://` 引用 |
| Context follow-up | 能回答 `68 Wh` |
| Unsupported claim | 明确拒答，不编造 |
| Session list/get | 能看到并读取 transcript |
| Session delete | 删除后 404 |
| Restart persistence | 重启后同 session 可继续 |
| QA validation | SSE `VALIDATION_ERROR` |
| Command validation | unified envelope `VALIDATION_ERROR` |
| Docs endpoints | OpenAPI、Swagger、ReDoc 可访问 |
| Secret safety | Postman 请求与保存响应中无供应商密钥 |

## 14. 注意事项

1. **不要在 Postman 中保存真实密钥。**
2. **不要执行全部 107 个命令。** 目录中包含破坏性和 privileged 命令。
3. **不要删除 `data/` 目录。** 其中包含 OpenViking 运行时数据与会话库。
4. 上传验证只使用 `postmanScope`，结束后通过 `rm` 清理该隔离命名空间。
5. SSE 需要等待 `done` 或 `error`，不要因为看到部分文本就提前停止请求。
6. 如果 Postman 缓冲导致看不到实时增量，但最终响应中事件完整，可以用 `curl.exe -N` 辅助观察实时流。
7. 模型回答措辞可能变化；验收重点是事实、引用、事件序列与终态，而不是要求逐字匹配示例输出。
