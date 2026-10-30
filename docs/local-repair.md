# 验证反馈驱动的局部参数修复

## 本次实现

失败步骤生成结构化反馈：步骤、工具、错误分类、错误详情、累计尝试次数和验证 verdict。
LLM 模式的 FeedbackReplanner 将这些反馈与输入产物 Schema、原参数和工具契约交给模型。
模型只能修改现有参数值，不能改变参数键、工具、产物引用、输出名称或 DAG 依赖。
首版支持 chart、aggregate 和 aggregate_pivot；后续增加 sql_query（见 task-semantics.md）。
其他工具在调用模型前拒绝修复。
修复后的列名还必须存在于输入 Schema；未满足条件的建议不执行。
修复在原图上进行，失败步骤尚未成功，因此其下游尚未执行；只重跑失败步骤即可复用上游结果。
尚未支持新增步骤、重写子图或修正已经成功但业务口径错误的步骤。

参数保留检查是结构约束，不保证修复的业务意义正确，后续仍执行现有验证器。
未宣称已实现目标级语义验证。模型调用失败、返回无效 JSON、未修改参数或改变产物引用时，
记录 repair_rejected 并终止该步骤；没有静默使用模板修复。

累计步骤尝试次数上限默认 3，运行内重规划上限默认 3，可通过 CLI 调整。
工具切换/参数修复不重置累计预算，避免原来的 aggregate 工具反复切换而无限运行。
这些是次数预算，不是整个运行的墙钟超时。

## 真实模型对照

2026-10-30，OpenCode Go / deepseek-v4.1-flash，证据：
`outputs/repair_dux77v60/results.json` 和 `checks.json`。

两组均使用同一模板规划器，故意将图表 y 列 revenue 改成 reveneu；
仅修复调用真实模型，初始计划不是模型生成的。两组均先按原策略重试一次。

| 组别 | 最终结果 | 失败/恢复步骤 | 上游重复执行 |
|---|---|---|---|
| 原工具替换机制 | finished_with_failures，无最终报告 | 图表失败，下游报告跳过 | 无 |
| 模型参数修复 | finished，生成报告 | 图表 recovered | 无 |

修复组执行序列：s1、s2、s3、s4、s4、s4、s5。
独立 Decimal 计算验证 24 个月 revenue 汇总正确。
这是单个受控故障的闭环演示，不能据此推断总体成功率提升。

## 复现

设置 Go 的 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL 后：

```powershell
.\.venv\Scripts\python.exe scripts/run_repair_validation.py
```

CLI 在 `--mode llm` 时自动启用反馈修复，mock 模式仍使用原工具替换机制。
下一步扩展至错误日期列、聚合参数、过滤条件等任务，并分别统计修复成功、误修复和调用成本。
