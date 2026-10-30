# 通用表格任务约束

新增 `tabular_aggregate`，不再要求固定的 customers/orders 字段。
既有 `customer_totals_year` 保持兼容。用户/开发者明确提供需求，不由模型自行定义正确答案。
规划器现在也收到 task_contracts，便于一次生成正确计划；参考计算仍独立于计划 SQL 和工具实现。

## 支持范围

| 项目 | 能力与明确语义 |
|---|---|
| 来源 | CSV 或 SQLite 单表；SQLite 只读，表名引用转义 |
| 筛选 | 多条件 AND；eq/ne/lt/lte/gt/gte；显式 text 或 number |
| 分组 | 多列；identity、month、year；日期转换要求 ISO 格式 |
| 指标 | sum、count（行数）、mean；可同时定义多个指标 |
| 输出 | 分组列与指标列的名称必须唯一；分组集合必须完全一致，无重复分组 |
| 数值比较 | Decimal；sum/mean 默认容差 0.005；count 必须精确相等 |
| 全局聚合 | group_by=[]；空数据 sum/count 为 0，空集 mean 不支持 |
| 异常输入 | 缺少列、无效日期、缺少分组值、NaN/Infinity、无效数值均失败 |

文本日期筛选按字典序，因此请使用可排序的统一 ISO 日期格式。不会自动清洗、去重、填补或删除
缺失记录；这些操作必须由任务需求明确表达或先准备可信参考数据。未支持 JOIN、自定义公式、
OR 筛选、任意日期解析、SQL NULL 聚合语义及报告文本事实检查。
数值分组字段的文本格式须与输出一致，当前不自动合并 `1` 与 `1.0`。
配置未知字段会被拒绝，避免 filters 拼写错误时悄悄跳过筛选。

参考引擎使用标准库读取记录并逐条筛选/分组/Decimal 运算，不调用 pandas 的聚合工具，
也不执行模型提供的参考 SQL。它会扫描并加载数据，当前面向小规模数据，未做大表优化。
验证支持的指标多于现有 aggregate 工具（仅 sum）的能力；某些任务需由 SQL 或其他工具实现，
并非所有合法契约都保证能被当前规划器执行或自动修复。

## 使用

示例：`docs/tabular-contracts.example.json`，按月统计 2024 年订单金额与数量。
相对 source 路径以 JSON 所在目录为基准。

```powershell
# 配置模型环境后运行；要求生成 monthly_orders 和 final_report
.\.venv\Scripts\python.exe -m agent "按月统计 orders.db 的 orders 表中2024年金额total和订单数n_orders，输出monthly_orders并生成final_report" --mode llm --task-contracts docs/tabular-contracts.example.json

# 严格真实模型规划与受控对照，无 mock 回退
.\.venv\Scripts\python.exe scripts/run_generic_semantic_validation.py
```

CLI 的一般 LLMPlanner 仍保留原有 mock 回退行为；上述严格验证脚本禁止回退。

## 本次真实验证

2026-10-30，OpenCode Go / deepseek-v4.1-flash。
证据：`outputs/generic_semantic_vddf0lk1/` 中 planning、report.md、results.json 与修复 calls.json。
先真实调用模型生成月度 SQL 计划，然后在各组复用同一份计划，避免规划差异干扰对照。
初始规划使用明确任务约束，没有模板回退；故障为人为注入。

| 场景 | 原检查 | 仅通用契约 | 通用契约 + 模型修复 |
|---|---|---|---|
| 正常模型计划 | 正确完成 | 正确完成 | 正确完成，0 次修复调用 |
| 筛选错移至 2025 年 | 错误接受 | 拦截 | 正确完成，1 次修复调用 |
| SUM 被改为 AVG | 错误接受 | 拦截 | 正确完成，1 次修复调用 |

单份数据、每组单次运行，不代表普遍成功率。CSV/SQLite 参数化测试另外覆盖数值筛选、
identity 分组、全局聚合、mean/count/sum，以及错分组、漏组、重复组、非有限值与非整数 count。
下一步应以更多独立数据集和未参与开发的任务评估泛化，而不是仅增加同类拼写案例。
