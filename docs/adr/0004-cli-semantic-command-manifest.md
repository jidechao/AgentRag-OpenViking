# 命令目录采用 CLI 语义命令并生成 manifest

命令面以本地 OpenViking CLI 的语义命令为准，而不是把 Server 的全部内部 HTTP operations 都暴露为稳定 API。开发阶段从 CLI 定义、SDK 签名和 OpenViking OpenAPI 生成 command manifest；运行时加载 manifest 并对照实际 OpenAPI 校验，既避免逐条手写路由，也能暴露能力漂移。
