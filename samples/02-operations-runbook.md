# Agentic RAG 示例：AURORA-LX-9001 运维 Runbook

- 目标命名空间：`viking://resources/agentic-rag-demo`
- 唯一运维标记：`AGENTIC-RAG-DEMO-RUNBOOK-5248`
- 并发索引任务上限为 4；第 5 个请求进入队列
- 索引队列等待超过 15 分钟时触发熔断，新的索引请求返回 `QUEUE_CIRCUIT_OPEN`
- 每日 02:00–03:00 执行增量一致性检查
- 出现资源不可读时，先执行 `system consistency`，再仅对报告的 URI 子树执行 `reindex`

## 故障处理

1. 查看 `observer queue`，确认队列深度和最早任务时间。
2. 若熔断已打开，停止新增索引请求并等待 5 分钟。
3. 修复底层资源可读性后，对不一致子树执行重建。
4. 复查 `system consistency`，确认无新增失败项。

## 边界

本 Runbook 只覆盖知识索引和一致性运维，不包含数据库备份、网络路由或应用发布。
