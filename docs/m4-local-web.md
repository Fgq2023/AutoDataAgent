# M4 本地 Web 与期中交付

## 启动

在仓库根目录安装可选 Web 依赖，然后启动单用户服务。

    .\.venv\Scripts\python.exe -m pip install -e ".[web]"
    .\.venv\Scripts\python.exe -m agent.web --data-root data --outputs-root outputs/web --port 8000

浏览器打开 http://127.0.0.1:8000 。服务默认仅监听本机。配置不同工作区时修改 data-root，运行目录应与数据目录分开。一个输出工作区只允许一个服务进程。

真实模型使用启动进程的 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL 环境变量。页面只显示配置状态和模型名。离线演示显式选择 Mock；Python 选择 Docker 时应先完成 M3 镜像构建。

## 页面流程

1. 输入目标，勾选工作区文件。TaskSpec 补充字段可以通过高级设置填写，questions 用于显式关键问题。
2. 选择规划模式和 Python 后端，启动分析。缺少澄清答案时显示问题，回答后继续执行。
3. 查看步骤依赖、状态、尝试次数、验证检查和恢复记录。运行可取消，取消会在核心执行边界生效。
4. 点击表格、图表、Markdown 报告查看预览。CSV 预览最多 100 行，报告预览最多 200000 字符，下载可获得完整产物。报告以安全的纯文本 Markdown 预览。
5. 下载证据 ZIP，包含计划清单、SQLite 状态、轨迹、产物版本和任务记录。运行停止后才能导出。
6. 服务重启后，执行中任务标为中断。打开历史任务，核对记录后选择恢复。恢复沿用 M3 的哈希校验、预算、模型身份和会话。

## 接口

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | /api/config | 工作区与模型配置状态 |
| GET | /api/sources | 工作区源数据 |
| GET / POST | /api/runs | 历史 / 创建任务 |
| GET | /api/runs/{id} | 状态、依赖计划、步骤和产物 |
| GET | /api/runs/{id}/events?after={seq} | 增量轨迹 |
| POST | /api/runs/{id}/clarify | 提交答案与来源选择 |
| POST | /api/runs/{id}/cancel | 协作式取消 |
| POST | /api/runs/{id}/resume | 恢复运行 |
| GET | /api/runs/{id}/artifacts/{artifact_id}/preview | 预览 |
| GET | /api/runs/{id}/artifacts/{artifact_id} | 原始产物 |
| GET | /api/runs/{id}/export | 证据 ZIP |

## 三段演示

全部使用显式 Mock 和合成数据，便于稳定复现。

- 正常演示。选择 sales.csv，输入“分析 sales.csv 的月度销售趋势，输出月度汇总表和趋势图”。可通过 TaskSpec 加一个问题，展示澄清后自主执行、表格、PNG、报告和 ZIP。
- 检测与恢复。高级设置选择“原聚合工具持续失败，切换实现”。轨迹显示 aggregate 失败、重试、重规划到 aggregate_pivot，独立答案保持一致。
- 中断恢复。使用自动演练脚本，在观察到成功的上游步骤后强制结束真实 HTTP 服务，再重启并经 API 恢复。对照轨迹确认成功上游没有重复执行。

自动执行两轮完整演练：

    .\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
    .\.venv\Scripts\python.exe scripts/rehearse_m4.py

脚本对汇总答案使用独立 Decimal 计算核对，同时检查 HTTP 预览、图表文件头、报告、数据库快照与 ZIP 内容。初次演练发现 SQLite 热日志必须允许恢复，已用真实子进程中断回归修复。

## 冻结安排

本阶段提供期中候选版本、八个功能案例入口、开发集基线、两轮完整演练和架构限制说明。2026 年 12 月 12 日按当前通过验收的提交标记演示冻结版本，之后只修阻塞问题。12 月 15 日提交的行政流程与导师反馈需按课程要求处理。

导出会同时持有服务锁与 M3 运行锁，避免 Web 或 CLI 恢复与打包并发。证据 ZIP 保存运行内记录与产物，外部源数据以路径和哈希追踪。未验证产物显示为待验证，预览与下载只允许成功检查点的产物，并复核内容哈希。
