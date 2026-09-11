# 采用通用命令执行器和稳定 Agent 事件契约

OpenViking 命令通过命令目录驱动的通用执行端点暴露，文件类命令在桥接层处理 multipart，避免为每个 CLI 语义命令手写独立路由。知识问答使用稳定 SSE event name 与 data.type 双重事件标识，REPL 和 REST 都消费同一 Agent Event Stream，只负责各自的展示编码。
