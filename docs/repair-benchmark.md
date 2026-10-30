# 受控故障修复评估

2026-10-30，OpenCode Go / deepseek-v4.1-flash。
证据：`outputs/repair_benchmark_n8psi4m9/report.md` 与 `results.json`。
完整模型请求、响应及用量在场景 feedback 目录的 `calls.json` 中，不含凭据。

## 实验设置

同一份 seeded 演示数据、相同的模板计划，对比原工具替换与模型参数修复。
初始计划不是模型生成的，模型只负责修复。每种故障单次运行，不用于估计总体正确率。
目标指定按月汇总 units/revenue 和 revenue 趋势图。
使用独立 Decimal 计算核对每个月两个指标的汇总，并检查最终图表参数和报告。
此处检查了图表参数和 PNG 文件格式，没有验证像素中实际绘制的数值。

| 故障 | 原工具替换 | 模型修复 | 修复调用 |
|---|---|---|---:|
| 图表 y 列 reveneu | 失败 | 答案正确 | 1 |
| 图表 x 列 mounth | 失败 | 答案正确 | 1 |
| 日期列 order_date（实际 date） | 失败 | 答案正确 | 1 |
| 分组列 mounth（目标 month） | 失败 | 答案正确 | 1 |
| 聚合列 reveneu | 失败 | 答案正确 | 1 |
| 正常对照 | 答案正确 | 答案正确 | 0 |
| 文件不存在 | 正确失败 | 正确失败 | 0 |

五个可恢复案例：模型修复 5/5，原工具替换 0/5；本次没有错误结果被接受。
所有案例上游重复执行次数为 0。缺失文件由框架拒绝，未测试模型自身的拒答能力。
原机制的工具互换不能修正参数，因此这组结果不能证明相对于其他参数修复方法更优。

模型返回 usage 共 3000 tokens（输入 2518，输出 482，包含服务报告的推理 token）。
五次调用耗时 2.7–5.5 秒，总约 20.3 秒；不包含工具执行时间。
服务端确认 billing_source 为 go；usage 中即时 cost 为 0，仍有 allowance 消耗，
不能解释为不占套餐额度。本次未做多次采样或置信区间估计。

## 本次加固

- chart 和 aggregation 之外的工具不自动修改参数。
- 允许模型返回 args=null；无效 JSON、未知列、引用/输出名称变更均拒绝。
- 聚合工具不再静默忽略部分错误列名，而是列出可用数值列并显式失败。
- LLMClient 保存最近一次调用的 usage、服务模型名、耗时、finish_reason。
- 回归测试 51 项通过；离线 benchmark 使用已知答案 oracle，只验证评估基础设施。
- CI 增加离线 benchmark 和证据上传，未在本次会话执行远端 CI。

## 复现

```powershell
# 无模型调用，仅检查评估与控制流程
.\.venv\Scripts\python.exe scripts/run_repair_benchmark.py

# 设置 OPENAI_* 进程环境变量后进行真实模型评估
.\.venv\Scripts\python.exe scripts/run_repair_benchmark.py --live
```

所有产物写入新的 outputs/repair_benchmark_* 目录。下一阶段需要加入“可执行但语义错误”
任务，如错误年份、错误聚合指标；当前错误触发修复机制尚不能可靠发现这类问题。
