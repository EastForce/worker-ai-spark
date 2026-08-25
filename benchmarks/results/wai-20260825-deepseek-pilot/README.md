# DeepSeek 24 题 pilot 待审包

> 状态：已完成机器运行与两路 AI 盲评，待项目负责人确认；不是正式排名或人工评审结论。

## 先看结论

- 3 个请求模型各完成 24 题，共 72/72 条成功回答；没有空回答、截断或 API 错误。
- 评分使用 `MiniMax-M2.1-highspeed` 与 `MiniMax-M3` 两路盲评。定向重试后，每个回答都有两条有效 AI 初评；原始 5 条无效评分仍保留，没有被覆盖或计入中位数。
- 三个模型的回答等权 AI 总分中位数均为 16/20；这不足以支持硬性排序。
- 当前有 3 条待人工复核的风险信号、28 个出现评分分歧的回答、0 条人工评分，因此不满足正式比较门槛。

完整覆盖、五维中位数、风险与分歧清单见 [evaluation-report.zh-CN.md](evaluation-report.zh-CN.md)。

## 运行边界

- 题库：`benchmarks/first-batch.zh-CN.jsonl` 的 24 个主问题；不包含追问。
- 被测模型：`deepseek-v4-flash`、`deepseek-v4-flash-vision-exp`、`deepseek-v4-pro`。
- 生成配置：无额外 system prompt，`max_tokens=8192`，显式关闭 DeepSeek thinking；详细配置和哈希见 `run-manifest.json`。
- 评测阶段：pilot；`formal_comparison_allowed=false`。

## 文件说明

- `records.jsonl`、`run-manifest.json`：72 条完整回答与运行清单。
- `package-manifest.json`：组包后的评分状态和关键文件哈希；`run-manifest.json` 保留生成阶段的原始 `scoring_status=unscored`，不追写历史。
- `scores-*.jsonl`：规范化 AI 初评分；`retry` 文件是新增评分者编号的定向重试。
- `score-summary.json`：回答等权汇总、无效记录、风险和分歧登记。
- `evaluation-report.zh-CN.md`：面向人工检查的中文技术报告。

本包从本地 `_pending` 审核区复制提升，原始审计 manifest 中可能仍记录其生成时的仓库相对路径；正式汇总和报告已在本目录重新生成。完整 judge 请求、评分提示和原始流式响应继续留在本地 `_pending` 审计区，未进入拟公开包；规范化评分文件仍保留其哈希绑定和校验状态。上传前的凭据值与本机绝对路径精确匹配检查均为 0。项目负责人仍应复核隐私、内容、版本与公开范围。
