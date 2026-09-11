# Agentic RAG Assistant

一个基于 Claude Agents SDK 与 OpenViking 的知识问答助手；REPL CLI 和 REST API 是同一 Agent Core 的两种使用入口。

## Language

**Agent Core**:
承载模型循环、OpenViking 工具调用、事件转换与会话延续的共享核心。
_Avoid_: 业务层、controller

**OpenViking Server**:
独立运行的 OpenViking 知识存储与检索服务，是资源、检索、任务和命令执行的权威边界。
_Avoid_: 内嵌数据库、本地 RAG引擎

**Server-backed OpenViking Command**:
由 OpenViking Server 实际提供能力支撑的命令；本地 CLI UI、语言选择和版本展示不属于这个概念。
_Avoid_: CLI 字面命令、任意 shell命令

**Knowledge Citation**:
回答中用于追溯依据的 OpenViking `viking://` 来源。
_Avoid_: 普通链接、无出处引用

**Persistent Conversation Session**:
可跨 REPL 轮次和服务进程重启延续的问答会话。
_Avoid_: 临时请求、一次性上下文

**Retrieval Observation**:
当 Agent 触发 OpenViking 检索时，面向 REPL 或 REST 消费方发出的可观测提示。
_Avoid_: 日志行、调试输出

**Command Envelope**:
REST 命令响应的统一外壳，用于表达成败、命令名、数据和请求标识。
_Avoid_: 裸返回值、供应商原始响应

**CLI Semantic Command**:
以本地 OpenViking CLI 定义为准的用户级命令，是命令目录的命名边界；debug、metrics、console 等内部 HTTP operation 不自动升格为命令。
_Avoid_: raw route、内部接口

**Command Manifest**:
由 CLI、SDK 与 OpenAPI 求交集或补差生成的命令注册表，用于驱动 REST 路由、参数校验和文档清单。
_Avoid_: 手写endpoint表、硬编码命令列表

**Demo Knowledge Namespace**:
隔离示例文档与既有知识的 OpenViking 资源命名空间。
_Avoid_: 默认资源根目录、共享测试数据

**Agent Event Stream**:
Agent Core 输出的稳定事件序列，REPL 将其渲染为终端提示，REST 将其编码为 SSE。
_Avoid_: 原始模型流、供应商事件
