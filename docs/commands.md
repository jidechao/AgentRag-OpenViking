# 命令目录与长任务语义

> 本页补充命令的长任务与中断语义；完整命令表以 [README 的「命令目录」一节](../README.md) 为准
> （由 `agentic_rag/command_manifest.json` 生成，与 `GET /commands` 返回的目录一致）。

## 长任务与中断语义

- `sync` 命令直接返回统一 envelope。
- `native_async` 命令立即返回本地 job，并尽可能保留 OpenViking-native task ID；`GET /jobs/{job_id}` 会向 Server 对账。若 Server 已直接返回最终结果且没有 task ID，本地 job 会记录为 succeeded。
- `blocking_wait` 与 `long_running` 命令立即返回 queued job，由本服务后台执行。
- job 状态为 `queued / running / succeeded / failed / interrupted`。
- 服务重启时，没有 OpenViking task ID 的本地任务重启后会被标记为 interrupted，错误码为 `JOB_INTERRUPTED`；有 native task ID 的 job 保留足够信息继续查询权威 Server 状态。
- 正常 shutdown 会取消仍在执行的本地任务；OpenViking 原生任务不会被伪造成本地成功，而以 Server 状态为准。
