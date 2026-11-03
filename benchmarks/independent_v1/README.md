# Independent-v1：公开数据上的独立任务集

2026-11-02 建立。30 个新编任务，数据与原 sales/orders 演示数据完全分离。
任务基于公开 UCI 数据，不是 UCI 官方发布的问题，也不是外部标准榜单。

| 拆分 | 数据域 | 本次实际导入行数 | 任务数 |
|---|---|---:|---:|
| dev | Bike Sharing，day/hour | 731 / 17379 | 12 |
| validation | Wine Quality，red/white | 1599 / 4898 | 8 |
| test | Energy Efficiency | 768 | 10 |

三个拆分的数据集家族完全分开。同一数据集的 CSV 与 SQLite 只是等价表示，不视为两个独立数据集。
Energy 是建筑模拟数据，不是实地能耗观测。各域均保留全部原记录，不去重、不删除负值或异常点。
仅做格式标准化：葡萄酒列名空格改下划线，能耗 X1…Y2 映射为 UCI 对应含义，
数字规范化；能耗工作簿去除全空尾行与尾列。原始 ZIP、作者、DOI、下载地址与 SHA256 都有记录。
原始来源均标注 CC BY 4.0，按来源许可归属，不随本仓库 MIT 代码许可变更。

## 下载与准备

```powershell
.\.venv\Scripts\python.exe scripts/prepare_independent_suite.py
```

从官方 UCI 下载约 0.45 MB ZIP，保存在 `outputs/independent_v1/raw/`。
准备五张 CSV 和各域 SQLite 数据库；大文件、派生产物与数值答案均不提交 Git。
后续准备会校验 `sources.lock.json` 的源文件哈希。首次已完成 30/30 参考一致性检查：
标准库逐行 Decimal 计算、CSV/SQLite 表示检查、SQLite SUM/AVG/COUNT 独立实现交叉核对。
这验证计算一致性，仍需人工审核问题与规格是否一致。

产物：

- `tasks.jsonl`：本目录与 outputs 各一份，不含答案/契约。
- `outputs/independent_v1/inputs/<domain>/`：Agent 可见源数据。
- `outputs/independent_v1/grader_only/`：评分契约和答案 CSV，不放进 Agent 的 data_root。
- `provenance.json`：数据来源、原始/标准化哈希、行数和列名。
- `reference_checks.json`：参考实现检查结果。

答案目录只是逻辑隔离，不是安全边界。正式盲测应在只挂载 inputs 的隔离环境中执行工具，
否则当前 Python 工具仍可能读取工作区其他文件。评估前会检查使用的源文件哈希，
并将任务 manifest 哈希与源数据版本写入结果。

## 评估方式

本次只建立任务和验证参考答案，**没有运行模型能力测试**，不能报告任务成功率。
后续开发冒烟测评与任务格式修订单独记录在 `docs/independent-dev-baseline-2026-11-02.md`。
首轮任务中的月份格式未明确，现已补充 YYYY-MM/YYYY；数值规格和保留测试题未改变。

配置 OPENAI_* 环境变量后，以下命令会发起真实模型调用，默认只运行 dev 前 3 个任务：

```powershell
# 只有自然语言问题和源数据；参考契约仅在评分侧使用
.\.venv\Scripts\python.exe scripts/run_independent_suite.py --split dev --limit 3

# 另一种实验条件：向规划/验证环节提供明确契约
.\.venv\Scripts\python.exe scripts/run_independent_suite.py --split dev --limit 3 --provide-contracts
```

两种条件必须分别报告，不能将向 Agent 提供评分规格后的成绩称为 NL-only 能力。
严格规划器不使用 mock 回退。保留测试需要显式 `--split test --allow-test`，避免误跑。
评分核对最终表格和报告存在性，不验证报告文字或图表视觉表达。

## 防泄漏和解释边界

- dev 用于提示词、工具和修复策略开发；validation 用于有限方案选择；test 在方案冻结后一次性评估。
- 不用 test 的答案、契约、失败轨迹或语义知识更新去训练/修复，再把它当作未见测试集。
- 本仓库拥有全部任务定义，不能声称对开发者盲测；公开原始数据也可能出现在模型预训练中。
- 当前任务是单表筛选/聚合/报告，覆盖现有契约能力，但没有 JOIN、复杂业务手册或开放分析。
- 只有 3 个数据域，任务共享操作模式，统计上并非 30 个完全独立样本。
- 首次正式测试前人工复核、冻结任务哈希和评估设置；测试反馈参与调优后需建立新保留集。

## 来源与进一步扩展

1. Fanaee-T, H. (2013). [Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset). DOI: 10.24432/C5W894.
2. Cortez et al. (2009). [Wine Quality](https://archive.ics.uci.edu/dataset/186/wine+quality). DOI: 10.24432/C56S3T.
3. Tsanas & Xifara (2012). [Energy Efficiency](https://archive.ics.uci.edu/dataset/242/energy+efficiency). DOI: 10.24432/C51307.

后续优先参考 [Adyen/Hugging Face DABstep](https://huggingface.co/datasets/adyen/DABstep)，
主测试答案隐藏，dev 包含答案，可先使用官方 dev 接入；CSV/JSON 加业务手册适合未来语义知识模块。
其输出格式与官方评分协议不同于本项目表格产物，不能直接把当前契约引擎当成官方评分器。
[官方设计说明](https://huggingface.co/blog/dabstep)。

[UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online+retail) 适合之后的清洗/退货口径任务，
但涉及取消订单、负数量、金额派生及缺失值。当前清洗工具默认删除负值，会把有效退货语义抹去，
因此本次没有把它直接加入主评测。

For the full development matrix, run each regime with `--limit 12 --repeats 3`.
Each process starts a fresh batch capped at 24 model requests, including repair
calls. Each task attempt is capped at eight requests. Keep every batch report.
If stopped, use its `next_offset` as `--offset` with the same split, limit,
repetition count and regime. Authentication, quota/rate limits and Ctrl+C stop
the batch after saving the attempt. The failed attempt remains in the evidence.
