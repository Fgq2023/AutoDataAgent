# AutoDataAgent 期中技术报告 · 2026-11-05 候选版

## 项目目标与当前成果

系统接收数据分析目标，检查 CSV、Excel、SQLite、JSON 和业务文档，通过计划、工具执行、验证、恢复和报告形成可追踪的工作流。期中候选版已具备 CLI 与本地 Web 两个入口，共享同一执行服务。真实模型采用 OpenAI-compatible API；离线演示明确标记为 Mock。

M1 建立开发测评，M2 补充 TaskSpec、任务契约、跨源连接和文档规则引用，M3 增加断点恢复与 Docker Python 隔离。M4 把这些能力接入单用户工作台，提供目标输入、数据选择、澄清、运行控制、过程检查与产物导出。

## 架构

    CLI / FastAPI + HTML + JavaScript
                   |
              execute_run / RunService
                   |
        TaskSpec -> profile -> planner
                   |
             Orchestrator
          /        |         \
      tools     verifier    recovery
          \        |         /
           SQLite + immutable artifacts
                   |
        report / preview / evidence ZIP

RunService 保存任务记录并串行执行单个活动任务。模型凭据只通过服务端环境配置。数据工具继续使用已有 registry；Web 不另行实现分析算法。

任务轨迹记录计划、模型请求、工具尝试、验证结果和恢复决定。运行清单保存模型身份、累计预算、输入哈希和计划修订。恢复时校验已验证产物，并将中断时未确定的步骤重新执行。SQLite 连接允许处理崩溃后遗留的热日志。

## 五项目标对应证据

| 项目目标 | 当前实现 | 可检查证据 |
|---|---|---|
| 多步骤任务分解 | Mock 与真实模型 DAG 规划，任务规格及列契约 | planning 模块、计划记录 |
| 数据工具交互 | Python、只读 SQL、加载、清洗、连接、聚合、绘图、报告 | tools 模块与工具轨迹 |
| 状态与执行历史 | SQLite 检查点、产物版本、来源链、跨进程恢复 | state.db、trace.json、恢复用例 |
| 基本可靠性验证 | 文件/结构检查、显式任务契约、独立源记录评分 | verification 模块与评测结果 |
| 代表任务评估 | 八个功能案例、独立开发题、受控错误恢复 | 下表及 M1/M2/M3 记录 |

## 八个固定功能验收案例

所有固定功能案例中的脏数据、客户表与政策文档均为合成数据。

| 类型 | 案例 | 主要测试入口 |
|---|---|---|
| 清洗 1 | 显式去重、缺失、非法负价格清洗 | tests/test_e2e.py 的 test_cleaning_scenario |
| 清洗 2 | 保留退款、删除坏日期与缺失/重复记录 | tests/test_m2_acceptance.py 的 test_cleaning_invalid_dates_and_signed_refunds |
| 表格问答 1 | JSON 筛选、计数与金额汇总 | tests/test_m2_acceptance.py 的 test_table_qa_filters_and_counts_json |
| 表格问答 2 | CSV + SQLite 按地区关联汇总 | tests/test_task_spec.py 的 test_csv_sqlite_representative_task |
| SQL 1 | 2024 年客户订单数与消费额 | tests/test_e2e.py 的 test_sql_scenario |
| SQL 2 | 2025 年边界与城市维度汇总 | tests/test_m2_acceptance.py 的 test_sql_year_boundary_by_city |
| 报告 1 | 月度汇总、趋势图和来源 | tests/test_e2e.py 的 test_trend_report_contains_summary_provenance_and_figure |
| 报告 2 | 跨源退款汇总、图表与 policy.md:2 引用 | tests/test_task_spec.py 的 test_csv_sqlite_representative_task |

同一个跨源工作流在表格与报告两类验收中检查不同输出属性。它们共享输入，不能视为完全独立的能力样本。

## 开发集基线

独立任务集包含 30 道新编问题，来自公开 UCI 数据。开发、验证和保留测试按数据域分开。开发组为 Bike Sharing，12 题 × 两种条件 × 三次重复，共 72 次任务尝试。

deepseek-v4.1-flash 的 NL-only 和显式约束条件均通过 34/36；模型请求分别为 36 和 38。四次失败主要涉及输出列和产物引用。显式约束条件使用评分规格，两组成绩仅描述该开发集上的观察。数据域共享、题型重复和公开数据潜在预训练暴露限制了泛化解释。

数值基线详见 docs/m1-dev-baseline-2026-11-03.md。验证集与保留测试继续留到方案选择和冻结后的正式评估。

## M4 演练与界面验收

两轮通过真实 localhost HTTP 服务演练正常任务、错误检测恢复与服务进程中断后的续跑。每轮均核对独立答案、表格预览、PNG、报告和 ZIP。证据位于 outputs/m4_rehearsal_jldgwo_f/results.json。

浏览器检查包含历史任务、依赖与恢复状态、图表预览，以及通过表单创建任务、回答澄清并完成分析。界面截图位于 outputs/m4-ui-proof.png。详细启动与演示步骤见 docs/m4-local-web.md。

## 适用范围与限制

系统当前为单用户、单服务进程、本地工作区原型。一次运行一个活动任务，采用轮询展示进度。取消在执行边界生效，正在运行的调用受各自超时约束。数据源选择来自配置工作区，产物通过数据库标识查询并校验路径。

TaskSpec 可以保存关键问题及回答；自动歧义检查目前覆盖来源选择等已实现规则。复杂业务语义仍依赖用户确认的规格。Python 可以使用 Docker 隔离；SQL、文件加载和其他受控工具在宿主执行。选择本地子进程意味着运行受信任代码。

普通 LLM 模式的模型或计划错误会明确失败。当前修复以参数修正和已有工具替换为主。开放任务、复杂文档理解、结构性子图调整及模型/语义模块集成属于后续阶段。初始规划未完成的运行保留失败轨迹，需重新发起。异常退出后的停机间隔保守计入剩余运行预算。

## 后续工作与冻结

M5 重点实现局部步骤插入与替换、受影响产物失效处理及报告数值证据关联。M6 接入成员的小模型与语义模块，M7 冻结方案后进行保留集、消融和论文实验。

本报告是提前完成的期中候选材料。12 月 12 日进行演示版本冻结，12 月 15 日按课程安排提交。两轮工程演练已完成，后续导师或团队彩排反馈继续进入版本记录。


## 真实模型 Web 验证

通过真实 HTTP 服务向 OpenCode Go 的 deepseek-v4.1-flash 提交相同的 CSV、SQLite 和 Markdown 任务，两次尝试均保留。第一次因模型将产物名称写入步骤依赖而被严格计划校验拒绝。第二次发生一次传输重试，累计两个 HTTP 请求，随后生成六步计划并完成 Docker 内 Python 聚合、图表和报告。独立核对 East=85、West=70，报告引用 policy.md:2。

失败记录为 outputs/m4_live_7s5xlock/results.json，成功记录为 outputs/m4_live_wgp5ztqh/results.json。此验证展示 Web 到真实模型和工具的完整链路；两次合成任务尝试不能作为泛化成功率。

## 工程复核

独立代码复核发现的导出/恢复并发，以及未验证产物可预览的问题均有先失败后通过的回归测试。导出持有跨进程运行锁，下载复核产物哈希。取消请求也会在下一个模型请求前生效，避免取消后继续发送传输重试。

依赖中的 Starlette 对 httpx 测试客户端发出弃用提醒，当前 HTTP 测试和实际浏览器行为通过。Web 包静态 HTML/CSS/JavaScript 已通过 wheel 内容检查。

最终完整回归为 144 项通过。FastAPI 服务生命周期和 HTTP 测试分别参考官方说明 https://fastapi.tiangolo.com/advanced/events/ 与 https://fastapi.tiangolo.com/tutorial/testing/ 。
