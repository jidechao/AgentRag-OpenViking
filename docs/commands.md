# 命令目录与长任务语义

> 本页是从 [README](../README.md) 抽出的完整参考，面向需要查阅全部命令的读者；
> 入门和快速开始请先看 README。

## 命令目录

命令表由 `agentic_rag/command_manifest.json` 生成。运行时还会对照实际 OpenAPI；缺失、意外或实质 schema 漂移会导致启动失败，非实质描述漂移记录为 warning。

| # | 命令 | Transport | 执行模式 | 文件输入 | 风险 | 说明 |
|---:|---|---|---|---|---|---|
| 1 | `health` | sdk | 同步 | 无 | 只读 | Run a quick server reachability check. |
| 2 | `status` | sdk | 同步 | 无 | 只读 | Show OpenViking server readiness and component status. |
| 3 | `find` | sdk | 同步 | 无 | 只读 | Retrieve relevant OpenViking context semantically. |
| 4 | `read` | sdk | 同步 | 无 | 只读 | Read exact Level 2 file content from a Viking URI. |
| 5 | `write` | sdk | OpenViking 原生异步 | 无 | 变更 | Update text content in an existing resource. |
| 6 | `add-resource` | sdk | OpenViking 原生异步 | 可选上传 | 变更 | Import a local file, folder, URL, repository, or whole website (sitemap/RSS) into OpenViking. |
| 7 | `add-skill` | sdk | OpenViking 原生异步 | 可选上传 | 变更 | Import a skill directory, SKILL.md file, or raw skill content. |
| 8 | `add-memory` | rest_fallback | OpenViking 原生异步 | 无 | 变更 | Add a memory directly from text or JSON messages. |
| 9 | `set-tags` | sdk | OpenViking 原生异步 | 无 | 变更 | Update explicit retrieval tags for a file or directory. |
| 10 | `ls` | sdk | 同步 | 无 | 只读 | List resources under a Viking URI. |
| 11 | `tree` | sdk | 同步 | 无 | 只读 | Show a hierarchical view of resources under a URI. |
| 12 | `mkdir` | sdk | 同步 | 无 | 变更 | Create a directory in OpenViking. |
| 13 | `rm` | sdk | OpenViking 原生异步 | 无 | 破坏性 | Remove a resource from OpenViking. |
| 14 | `cp` | rest_fallback | 同步 | 无 | 变更 | Copy a file or directory without reparsing or regenerating vectors. |
| 15 | `mv` | sdk | 同步 | 无 | 变更 | Move or rename a resource. |
| 16 | `stat` | sdk | 同步 | 无 | 只读 | Show metadata for one resource. |
| 17 | `attrs get` | rest_fallback | 同步 | 无 | 只读 | Get or update logical extended attributes for a resource. |
| 18 | `attrs set-tags` | sdk | OpenViking 原生异步 | 无 | 变更 | Get or update logical extended attributes for a resource. |
| 19 | `get` | sdk | 同步 | 输出文件 | 只读 | Download a file resource to a local path. |
| 20 | `search` | sdk | 同步 | 无 | 只读 | Run experimental context-aware retrieval, optionally scoped to a session. |
| 21 | `grep` | sdk | 同步 | 无 | 只读 | Search resource content with a text pattern. |
| 22 | `glob` | sdk | 同步 | 无 | 只读 | Find resources by glob pattern. |
| 23 | `abstract` | rest_fallback | 同步 | 无 | 只读 | Read Level 0 abstract content for a directory. |
| 24 | `overview` | sdk | 同步 | 无 | 只读 | Read Level 1 overview content for a directory. |
| 25 | `wait` | sdk | 本地后台等待 | 无 | 只读 | Wait for queued async processing to complete. |
| 26 | `reindex` | sdk | OpenViking 原生异步 | 无 | 变更 | Reindex semantic/vector artifacts for a URI. |
| 27 | `import` | sdk | OpenViking 原生异步 | 必须上传 | 变更 | Import an .ovpack into a target URI. |
| 28 | `export` | sdk | 本地长任务 | 输出文件 | 只读 | Export context from a URI as an .ovpack file. |
| 29 | `backup` | sdk | 本地长任务 | 输出文件 | 只读 | Create a restore-only backup .ovpack for public OpenViking scopes. |
| 30 | `restore` | sdk | OpenViking 原生异步 | 必须上传 | 变更 | Restore a backup .ovpack to its original public scope roots. |
| 31 | `chat` | rest_fallback | 同步 | 无 | 变更 | Chat with the vikingbot agent. |
| 32 | `compile` | rest_fallback | OpenViking 原生异步 | 无 | 变更 | Use a required VikingBot Skill to compile OpenViking materials into Wiki pages or a Skill package. |
| 33 | `skills add` | sdk | OpenViking 原生异步 | 可选上传 | 变更 | Add skills from a source |
| 34 | `skills list` | sdk | 同步 | 无 | 只读 | List installed agent skills |
| 35 | `skills find` | sdk | 同步 | 无 | 只读 | Find installed agent skills semantically |
| 36 | `skills show` | sdk | 同步 | 无 | 只读 | Show one installed skill |
| 37 | `skills update` | sdk | OpenViking 原生异步 | 可选上传 | 变更 | Update installed skills from their recorded source |
| 38 | `skills remove` | sdk | 同步 | 无 | 破坏性 | Remove installed skills |
| 39 | `skills validate` | sdk | 同步 | 可选上传 | 只读 | Validate a local SKILL.md file or skill directory |
| 40 | `acl get` | sdk | 同步 | 无 | 只读 | Get or update access permissions for a resource.: get |
| 41 | `acl set` | sdk | 同步 | 无 | 变更 | Get or update access permissions for a resource.: set |
| 42 | `acl grant` | sdk | 同步 | 无 | 变更 | Get or update access permissions for a resource.: grant |
| 43 | `acl revoke` | sdk | 同步 | 无 | 变更 | Get or update access permissions for a resource.: revoke |
| 44 | `acl rm` | sdk | 同步 | 无 | 破坏性 | Get or update access permissions for a resource.: rm |
| 45 | `task status` | sdk | 同步 | 无 | 只读 | Show status of a specific task |
| 46 | `task cancel` | sdk | 同步 | 无 | 破坏性 | Cancel a task |
| 47 | `task list` | sdk | 同步 | 无 | 只读 | List all tracked tasks |
| 48 | `task watch ls` | sdk | 同步 | 无 | 只读 | List watch tasks (auto-refresh subscriptions) |
| 49 | `task watch show` | sdk | 同步 | 无 | 只读 | Show details of a single watch task |
| 50 | `task watch rm` | sdk | 同步 | 无 | 破坏性 | Delete a watch task |
| 51 | `task watch pause` | rest_fallback | 同步 | 无 | 变更 | Pause a watch task (preserves cadence, stops scheduling) |
| 52 | `task watch resume` | rest_fallback | 同步 | 无 | 变更 | Resume a paused watch task |
| 53 | `task watch update` | sdk | 同步 | 无 | 变更 | Update one or more mutable fields of a watch task. At least one flag is required |
| 54 | `task watch trigger` | sdk | 同步 | 无 | 变更 | Trigger an immediate refresh, bypassing the schedule |
| 55 | `session new` | sdk | 同步 | 无 | 变更 | Create a new session |
| 56 | `session list` | sdk | 同步 | 无 | 只读 | List sessions |
| 57 | `session get` | sdk | 同步 | 无 | 只读 | Get session details |
| 58 | `session get-session-context` | sdk | 同步 | 无 | 只读 | Get full merged session context |
| 59 | `session get-session-archive` | sdk | 同步 | 无 | 只读 | Get one completed archive for a session |
| 60 | `session delete` | sdk | 同步 | 无 | 破坏性 | Delete a session |
| 61 | `session add-message` | sdk | 同步 | 无 | 变更 | Add one message to a session |
| 62 | `session add-messages` | sdk | 同步 | 无 | 变更 | Add multiple messages to a session |
| 63 | `session config set` | sdk | 同步 | 无 | 变更 | Set mutable session configuration |
| 64 | `session commit` | sdk | OpenViking 原生异步 | 无 | 变更 | Commit a session (archive messages and extract memories) |
| 65 | `snapshot commit` | sdk | 同步 | 无 | 变更 | Commit the current workspace state as a new snapshot. |
| 66 | `snapshot restore` | sdk | 同步 | 无 | 破坏性 | Restore a project directory to a past snapshot via a forward commit. |
| 67 | `snapshot show` | sdk | 同步 | 无 | 只读 | Show a commit's metadata, or a single blob at a path. |
| 68 | `snapshot log` | sdk | 同步 | 无 | 只读 | Walk commit history for a branch, newest first. |
| 69 | `snapshot diff` | sdk | 同步 | 无 | 只读 | Compare one file between two snapshots as a unified diff. |
| 70 | `snapshot ignore-get` | sdk | 同步 | 无 | 只读 | Show the account-level .ovgitignore content. |
| 71 | `snapshot ignore-set` | sdk | 同步 | 无 | 变更 | Set the account-level .ovgitignore content (overwrites). |
| 72 | `snapshot ignore-delete` | sdk | 同步 | 无 | 破坏性 | Delete the account-level .ovgitignore file (idempotent). |
| 73 | `privacy categories` | rest_fallback | 同步 | 无 | 只读 | List privacy config categories |
| 74 | `privacy list` | rest_fallback | 同步 | 无 | 只读 | List targets by category |
| 75 | `privacy get` | rest_fallback | 同步 | 无 | 只读 | Get current active config for target |
| 76 | `privacy upsert` | rest_fallback | 同步 | 无 | 变更 | Upsert privacy config values |
| 77 | `privacy versions` | rest_fallback | 同步 | 无 | 只读 | List versions for target |
| 78 | `privacy version` | rest_fallback | 同步 | 无 | 只读 | Get one version by number |
| 79 | `privacy activate` | rest_fallback | 同步 | 无 | 变更 | Activate a version |
| 80 | `admin create-account` | sdk | 同步 | 无 | 特权 | Create a new account with its first admin user |
| 81 | `admin list-accounts` | sdk | 同步 | 无 | 特权 | List all accounts (ROOT only) |
| 82 | `admin delete-account` | sdk | 同步 | 无 | 特权 | Delete an account and all associated users (ROOT only) |
| 83 | `admin migrate` | sdk | OpenViking 原生异步 | 无 | 特权 | Migrate legacy agent/session data to user-owned namespaces (ROOT only) |
| 84 | `admin register-user` | sdk | 同步 | 无 | 特权 | Register a new user in an account |
| 85 | `admin list-users` | sdk | 同步 | 无 | 特权 | List all users in an account |
| 86 | `admin create-group` | sdk | 同步 | 无 | 特权 | Create an empty account-scoped group |
| 87 | `admin list-groups` | sdk | 同步 | 无 | 特权 | List groups in an account |
| 88 | `admin list-group-members` | sdk | 同步 | 无 | 特权 | List the users in a group |
| 89 | `admin add-group-member` | sdk | 同步 | 无 | 特权 | Add an existing account user to a group |
| 90 | `admin remove-group-member` | sdk | 同步 | 无 | 特权 | Remove a user from a group |
| 91 | `admin delete-group` | sdk | 同步 | 无 | 特权 | Delete an empty group |
| 92 | `admin remove-user` | sdk | 同步 | 无 | 特权 | Remove a user from an account |
| 93 | `admin set-role` | sdk | 同步 | 无 | 特权 | Change a user's role (ROOT only) |
| 94 | `admin regenerate-key` | sdk | 同步 | 无 | 特权 | Regenerate a user's API key (old key immediately invalidated) |
| 95 | `admin set-account-settings` | rest_fallback | 同步 | 无 | 特权 | Update allowlisted settings for an account |
| 96 | `observer queue` | rest_fallback | 同步 | 无 | 只读 | Get queue status |
| 97 | `observer vikingdb` | rest_fallback | 同步 | 无 | 只读 | Get VikingDB status |
| 98 | `observer models` | rest_fallback | 同步 | 无 | 只读 | Get models status (VLM, Embedding, Rerank) |
| 99 | `observer retrieval` | rest_fallback | 同步 | 无 | 只读 | Get retrieval quality metrics |
| 100 | `observer filesystem` | rest_fallback | 同步 | 无 | 只读 | Get filesystem operation metrics |
| 101 | `observer system` | rest_fallback | 同步 | 无 | 只读 | Get overall system status |
| 102 | `system wait` | sdk | 本地后台等待 | 无 | 只读 | Wait for queued async processing to complete |
| 103 | `system status` | sdk | 同步 | 无 | 只读 | Show component status |
| 104 | `system health` | sdk | 同步 | 无 | 只读 | Quick health check |
| 105 | `system consistency` | sdk | 同步 | 无 | 只读 | Check filesystem and vector-index consistency for a URI subtree |
| 106 | `system backend sync-status` | rest_fallback | 同步 | 无 | 只读 | Show multi-write backend sync status for a URI subtree |
| 107 | `system backend sync-retry` | rest_fallback | 同步 | 无 | 变更 | Retry pending multi-write backend sync work for a URI subtree |

## 长任务与中断语义

- `sync` 命令直接返回统一 envelope。
- `native_async` 命令立即返回本地 job，并尽可能保留 OpenViking-native task ID；`GET /jobs/{job_id}` 会向 Server 对账。若 Server 已直接返回最终结果且没有 task ID，本地 job 会记录为 succeeded。
- `blocking_wait` 与 `long_running` 命令立即返回 queued job，由本服务后台执行。
- job 状态为 `queued / running / succeeded / failed / interrupted`。
- 服务重启时，没有 OpenViking task ID 的本地任务重启后会被标记为 interrupted，错误码为 `JOB_INTERRUPTED`；有 native task ID 的 job 保留足够信息继续查询权威 Server 状态。
- 正常 shutdown 会取消仍在执行的本地任务；OpenViking 原生任务不会被伪造成本地成功，而以 Server 状态为准。
