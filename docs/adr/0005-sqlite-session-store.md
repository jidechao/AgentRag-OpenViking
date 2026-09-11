# 使用 SQLite 持久化 Claude 会话

自定义 Claude Agent SDK SessionStore 将 transcript entry 原样保存到本地 SQLite，使用 WAL 模式并通过后台线程执行阻塞数据库操作。SQLite 提供原子追加、会话列表和按 ID 恢复能力，且不需要新增异步数据库依赖。
