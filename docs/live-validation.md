# 真实模型规划链路验证

2026-10-30 最终实测：**OpenCode Go 的真实模型规划与执行链路已跑通，三个场景全部通过独立答案检查，无 mock 回退。**

## Go 套餐成功结果

- 模型：`deepseek-v4.1-flash`。
- 基础地址：`https://opencode.ai/inference/go/openai/v1`。
- 证据：`outputs/live_dwzz44f2/results.json`，以及各场景目录的请求、原始模型回复、任务计划、执行轨迹、报告和结果。
- 趋势、清洗、SQL：**3/3 PASS**；这是指定目标与产物名称的冒烟测试，不代表开放任务正确率。
- 本地回归测试：**43 passed**。

Go 官方文档：https://opencode.ai/v2/docs/console/go/
Go 支持其他产生类似请求的 coding agents，要求应用自身 User-Agent 及每个会话稳定的
`x-opencode-session`。客户端现已提供这些请求头，没有伪装为 OpenCode。
本验证验证了技术可调用性；持续使用时应遵守套餐对 coding agent 流量的适用范围与额度。

首次 Go 调用缺少会话头时返回 HTTP 400 MissingSessionID。补齐后模型成功生成计划，
但执行暴露数据上下文未提供真实路径的问题；已在 LLMPlanner 提示词数据中加入 `path`，
复验三个场景全部通过。此前仅按量计费接口返回余额不足，不能据此判断 Go 订阅不可用。

## 此前按量接口尝试

| 调用 | 结果 |
|---|---|
| 当前 inference 地址，默认 Python User-Agent | HTTP 403，边缘服务 error code 1010 |
| 设置应用 User-Agent，Zen 兼容地址，minimax-m2.5 | HTTP 403，Model access is disabled |
| 官方模型列表 | HTTP 200，可读取模型元数据；不代表账户有推理权限 |
| 当前 inference 地址，deepseek-v4.1-flash | HTTP 402，Insufficient account funds |

脱敏运行记录位于 `outputs/live_65idh132/results.json` 和
`outputs/live_dk8gzlki/results.json`。未获得模型文本响应或有效任务图，未使用 mock 回退。
凭据仅通过进程环境使用，没有保存到项目文件。

依据官方文档：https://opencode.ai/v2/docs/console/models/
当前兼容 Chat Completions 的基础地址为
`https://opencode.ai/inference/openai/v1`；免费聊天模型要求 OpenCode 客户端。

## 复跑

使用 Go 所属工作区的 API key，在进程环境设置：

```powershell
$env:OPENAI_BASE_URL = 'https://opencode.ai/inference/go/openai/v1'
$env:OPENAI_MODEL = 'deepseek-v4.1-flash'
# 在本地安全设置 OPENAI_API_KEY，不要将真实 key 提交到 Git。
.\.venv\Scripts\python.exe scripts/run_live_validation.py
```

脚本在新的 `outputs/live_*` 目录生成独立演示数据，依次验证趋势、清洗和 SQL。
每个场景调用现有 LLMClient 和 LLMPlanner 的生成/校验逻辑，再执行完整主循环。
严格规划器禁止 mock 回退；任何 API、解析、校验或结果核对失败都会导致非零退出。
认证/计费 HTTP 错误会提前停止，避免重复失败请求。

保存内容包括请求提示词、模型原始文本、有效计划（如产生）、运行摘要、
独立答案检查与执行轨迹。为复用独立答案检查，目标提示词规定了产物和结果列名；
因此这是接口和三个指定任务的冒烟测试，不是开放任务基准。
