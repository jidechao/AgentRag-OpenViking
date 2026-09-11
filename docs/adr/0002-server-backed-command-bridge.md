# 命令桥接采用服务器命令全量口径

OpenViking REST 化覆盖所有 Server-backed 命令：优先调用已安装 Python SDK；SDK 缺失但本地 Server Router 实际支持的命令通过 OpenViking REST API 补差。纯本地 CLI 管理命令和交互式 TUI 不伪装成 Server 命令暴露。该口径避免把 SDK 方法列表误当成能力边界，同时不引入本机 CLI 进程管理。
