# 接入 OpenViking 原生 MCP 端点

Agent Core 通过 OpenViking Server 内置的 `/mcp` HTTP MCP 端点接入检索、读取、写入和资源工具，而不是把 OpenViking SDK 再包装成自建 MCP server，也不是仅用 Agent Skill 提供使用说明。这样复用 OpenViking 已有能力，并让 Claude Agents SDK 的 tool call / tool result 事件自然进入统一事件流。
