# 现有场景运行基线

验证日期：2026-10-30。基础提交：`171fa1aae44f3a5871dbacbe266589741c843217`。

## 结果

在 Windows / Python 3.14.0 的项目 `.venv` 中完成验证：

- 原有测试套件：**41 passed in 6.29s**。
- `examples/demo_trend.py`：正常趋势、瞬时失败重试、永久工具失败重规划均完成。
- `examples/demo_cleaning.py`、`examples/demo_sql.py`：均完成。
- 新增的一键基线：**6/6 检查通过**。

| 场景 | 结果核对 | 恢复行为 |
|---|---|---|
| 月度趋势 | 24 个月，units/revenue 与独立 Decimal 累加一致；PNG 文件头有效 | 无 |
| 清洗 | 148 → 136 行，逐行核对字段和数值 | 无 |
| SQL | 2024 年 6 位客户的订单数与金额与独立计算一致 | 无 |
| 瞬时失败 | 最终汇总正确 | 1 次 retry |
| 原工具永久失败 | 替代工具汇总正确 | 1 次 retry、1 次 replan |
| 无替代工具 | 正确报告 1 个失败，跳过 1 个下游步骤，无最终报告 | 尝试恢复后终止 |

“无替代工具”通过指正确处理预期失败，不代表该分析任务成功完成。

## 复现

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts/run_baseline.py
```

从新环境安装可先运行 `python -m venv .venv`，再安装 `.[dev]`。
本次完整依赖版本见 `docs/baseline-requirements.txt`；它是 Python 3.14
环境快照，不是跨 Python 版本的通用锁文件。

本机原配置的 pip 镜像连接失败，改用 `--index-url https://pypi.org/simple`
完成安装。没有修改全局 pip 配置。若沙箱默认临时目录不可写，可将进程的
`TEMP` 和 `TMP` 指向仓库中的 `outputs/tmp` 后重试。

本次证据目录：`outputs/baseline_phyo4fqh/`。其中 `report.md` 是汇总，
`results.json` 保存环境版本、源文件与数据 SHA256、每个场景的检查和运行摘要。
场景子目录保留产物、执行轨迹和 SQLite 状态。该目录由 `.gitignore` 排除；
脚本每次新建证据目录，不覆盖现有数据。CI 已增加基线运行和证据上传步骤，
本次仅在本地验证，尚未执行远端 CI。

## 解释边界与下一步

本次使用显式 mock planner，不调用模型、不测试训练效果或语义演化。
这是现有模板场景的回归基线，不能解释为任意自然语言任务成功率。
图表检查验证文件格式，不评估视觉表达质量；计时是单次本地结果。

下一步优先增加模板外任务与确定性答案，并接入真实规划模型建立独立基线，
记录规划失败与 mock 回退。之后再评估验证反馈和局部重规划的收益。
