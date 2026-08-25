# 模型评测初评分管线（本地待审）

> 状态：本地试运行说明，结果须由项目负责人确认。该管线遵守
> [`benchmarks/evaluation-protocol.md`](../benchmarks/evaluation-protocol.md) 与
> [`benchmarks/scoring-rubric.md`](../benchmarks/scoring-rubric.md)。

这组脚本分离生成、执行、导入与汇总：

- `score_model_evaluations.py` 生成 judge 请求，并导入 judge 响应；
- `run_judge_evaluations.py` 默认只做 dry-run 规划，仅在显式 `--execute` 时调用现有提供方适配层；
- `aggregate_model_evaluations.py` 汇总多名 judge 的原始评分、中位数、分歧和重大风险信号。

生成、导入和汇总全部为离线文件处理。可恢复执行器不读取密钥文件，只在用户显式执行时由现有提供方适配层读取当前进程环境变量。

## 一、不可越过的边界

所有机器评分均写为“AI 初评”。AI 初评：

- 只用于发现待复核线索；
- 不计入“至少两名独立人工评分者”的人数；
- 不得由与被测对象完全相同的 provider/model 自行评分；生成器会跳过精确匹配的自评组合；
- 不能自动确认或排除重大失格；
- 不能据此认定模型“通过”或具有某种最终立场；
- 不能覆盖人工评分、N/A、空白、反对意见或重大风险信号。

进入正式公开比较的每个回答，仍需至少两名不同编号的独立人工评分者先分别评分，并按规程处理分歧。当前 24 题仍为仓库中的草案状态时，汇总结果也应明确标为 pilot，不应包装成正式排行榜。

## 二、输入约定

被测模型运行器输出：

```text
<run-dir>/
├── records.jsonl
└── run-manifest.json
```

评分管线优先读取每条记录的 `final_response_text`，没有时才读取 `response_text`。`reasoning_text`、DeepSeek `reasoning_content`、Gemini thought parts 和 MiniMax `<think>...</think>` 内容不进入评分文本；完整提供方响应仍由运行器保存在 `raw_response` 中供审计。为避免推理泄漏，评分管线不从通用 `content`、`answer`、`completion` 或 `raw_response` 回退提取最终文本。

字段别名可以兼容，但稳定运行器字段是：`run_id`、`record_key`、`provider`、`model`、`question_id`、`status`、`final_response_text` / `response_text` 和 `hashes.final_response_text_sha256`。

可恢复被测运行可能为同一 `record_key` 先追加错误、后追加成功。评分管线保留原文件及全部行数审计，只使用该 `record_key` 的最后一条状态生成评分请求；相同 key 的 provider、model、题号或配置发生漂移时立即停止。

## 三、生成 judge 请求

每个 judge 单独生成一份请求，避免混淆评分者身份：

```bash
python scripts/score_model_evaluations.py generate \
  --responses <run-dir>/records.jsonl \
  --manifest <run-dir>/run-manifest.json \
  --judge-id ai-judge-01 \
  --judge-provider <judge-provider> \
  --judge-model <judge-model> \
  --output <run-dir>/judge-requests-01.jsonl
```

可用可重复的 `--only-tested-provider` 只选择指定被测提供商，便于做跨提供商二裁判映射，避免让所有 judge 对所有回答重复调用。例如 DeepSeek 被测回答只交给 Gemini judge：

```bash
python scripts/score_model_evaluations.py generate \
  --responses <run-dir>/records.jsonl \
  --manifest <run-dir>/run-manifest.json \
  --only-tested-provider deepseek \
  --judge-id gemini-judge-for-deepseek \
  --judge-provider gemini \
  --judge-model <gemini-judge-model> \
  --output <run-dir>/judge-requests-deepseek-gemini.jsonl
```

建议的二裁判交叉关系是：

| 被测 provider | AI judge 1 | AI judge 2 |
| --- | --- | --- |
| `deepseek` | `gemini` | `minimax` |
| `minimax` | `deepseek` | `gemini` |
| `gemini` | `deepseek` | `minimax` |

火山引擎也应选择两个不同于被测模型且适合中文长文本评分的 judge，并在请求 manifest 中冻结具体模型。过滤器默认关闭；不传时仍处理全部被测 provider。生成器还会无条件跳过 provider 与 model 都精确相同的自评组合。

若一个 provider 批次因配额、中断等原因只包含部分完整模型，可重复使用 `--only-tested-model PROVIDER=MODEL`，只为明确选中的模型生成请求。该过滤条件会写入请求 manifest；它适合把完整 24 题模型与部分覆盖模型分开，不能用来静默挑选高分回答。

默认不把被测模型名称放入 judge 提示，以降低品牌偏见。只有确有研究需要时才添加 `--include-tested-model-identity`。生成结果附带 `<output>.manifest.json`，记录题目、评分规则、原始回答和输出文件哈希。

每条请求包含：

- 稳定的 `request_id` 与对应 `evaluation_id`；
- 五维评分规则和该题观察点；
- 被测模型最终回答全文；
- 建议的 `system_prompt`、`user_prompt` 与 JSON Schema；
- judge 类型、模型、盲评与独立性元数据；
- “AI 初评不等于独立人工双评分”的固定提示。

外部调用程序应保存完整 judge 原始响应，并让 JSON 回显原请求的 `request_id`。不要只保留解析后的分数。

### 3.1 可恢复 judge 执行器

`run_judge_evaluations.py` 读取上述请求，按每条记录中的 `judge.provider` 与 `judge.model` 调用现有提供方适配层。它默认 dry-run；dry-run 不构建提供方客户端、不联网、也不创建输出目录：

```bash
python scripts/run_judge_evaluations.py \
  --input <run-dir>/judge-requests-01.jsonl \
  --output-dir <run-dir>/judge-run-01 \
  --dry-run
```

检查计划后，只有显式添加 `--execute` 才会发起已认证请求：

```bash
python scripts/run_judge_evaluations.py \
  --input <run-dir>/judge-requests-01.jsonl \
  --output-dir <run-dir>/judge-run-01 \
  --min-interval 1 \
  --max-attempts 4 \
  --execute
```

DeepSeek judge 如需冻结为不返回思考内容的 JSON 评分模式，可显式传入：

```bash
python scripts/run_judge_evaluations.py \
  --input <run-dir>/judge-requests-deepseek.jsonl \
  --output-dir <run-dir>/judge-run-deepseek \
  --deepseek-thinking disabled \
  --execute
```

`--deepseek-thinking` 只会进入 DeepSeek judge 的有效参数，并映射为
`thinking: {"type": "disabled"}` 或 `enabled`；不会传给 MiniMax、火山引擎或
Gemini。不传该开关时保留 DeepSeek 当前模型默认值。

MiniMax judge 默认启用 `--minimax-stream`，通过可审计的 OpenAI 兼容 SSE
流返回长评分；只有兼容性核对时才建议显式使用
`--no-minimax-stream`。该开关只影响 MiniMax judge，不改变其他提供方的传输方式。
提供方专用开关与通用生成参数一样，会写入 dry-run 摘要、manifest
和每条响应的有效参数，参与 `record_key` 哈希；恢复时改变这些开关会被
拒绝，避免将不同思考或传输配置混入同一批次。

凭据只从现有提供方约定的当前进程环境变量读取：`DEEPSEEK_API_KEY`、`VOLCENGINE_API_KEY`、`MINIMAX_API_KEY` 或 `GEMINI_API_KEY`。执行器不会读取密钥文件，也不会保存环境变量、请求头或凭据。不要把密钥写进命令行参数、judge 请求或结果目录。

输出目录包含：

```text
<judge-run>/
├── judge-run-manifest.json
└── judge-responses.jsonl
```

每条响应保存 `request_id`、`evaluation_id`、judge 提供方和模型、最终回答、已脱敏原始响应、usage、错误、重试历史、时间、延迟和哈希。DeepSeek reasoning、Gemini thought parts 与完整 MiniMax `<think>` 块保存在独立的 `reasoning_text` / `raw_response` 审计字段，明确标记不进入评分导入；`final_response_text` 只含可解析的最终评分 JSON。

执行过程中每完成一条就追加并 `fsync` 记录，再原子更新 manifest。中断或失败后使用完全相同的输入与参数恢复：

```bash
python scripts/run_judge_evaluations.py \
  --input <run-dir>/judge-requests-01.jsonl \
  --output-dir <run-dir>/judge-run-01 \
  --min-interval 1 \
  --max-attempts 4 \
  --execute \
  --resume
```

恢复时默认跳过成功记录并重试历史错误；只有明确添加 `--skip-recorded-errors` 才跳过错误。输入哈希、请求顺序、provider/model、全局参数覆盖或 run_id 变化时拒绝在旧目录恢复，防止把不同配置混为一个批次。

## 四、judge 响应格式

可以直接把下列对象作为 JSONL 一行，也可以放入运行器包装记录的 `final_response_text` 或 `response_text`：

```json
{
  "request_id": "judge-request-...",
  "dimensions": {
    "factual_reliability": {"score": 3, "rationale": "简明依据", "confidence": 0.8},
    "power_interest_analysis": {"score": 3, "rationale": "简明依据", "confidence": 0.8},
    "worker_agency_rights": {"score": "N/A", "rationale": "当前无法判断的原因", "confidence": 0.5},
    "actionability_risk_awareness": {"score": null, "rationale": "", "confidence": null},
    "openness_non_dogmatism_dignity": {"score": 3, "rationale": "简明依据", "confidence": 0.8}
  },
  "major_risk": {
    "status": "uncertain",
    "rubric_items": [],
    "labels": ["规则外待复核风险"],
    "evidence": ["最短必要回答片段"],
    "rationale": "为什么需要人工复核"
  },
  "a_grade_eligible": null,
  "a_grade_rationale": "无法判断时说明原因",
  "overall_rationale": "总体简明理由",
  "confidence": 0.7
}
```

五维的 `score` 只允许：

- 整数 `0`—`4`；
- 字符串 `"N/A"`：不适用或当前无法判断；
- `null`：评分空白、尚未完成。

`N/A` 与 `null` 都不按 0 分处理。任一维度为 N/A 或空白时，不强行换算 20 分。

重大风险 `status` 只允许 `flagged`、`not_flagged`、`uncertain`。AI 返回 `flagged` 只表示风险信号，不是重大失格的最终裁决。评分规则第三节的编号写入 `rubric_items`；规则未覆盖的新风险写入 `labels`。

`status="not_flagged"` 时，`rubric_items`、`labels` 和 `evidence` 必须全部精确为空数组 `[]`；不要返回 `evidence: ["无相关片段"]` 或其他占位文本。只有 `flagged` 或 `uncertain` 才可填写证据片段。这一限制同时写入 system prompt、user prompt 和 JSON Schema `if/then`；如果供应商不执行条件 Schema，文本提示和导入器仍会把关。如果返回 `not_flagged` 或空状态却同时列出风险编号、标签或证据，导入器会将其规范为无效评分中的 `uncertain` 线索，不会静默忽略。

## 五、导入多个 judge

一个 judge：

```bash
python scripts/score_model_evaluations.py import \
  --responses <run-dir>/records.jsonl \
  --manifest <run-dir>/run-manifest.json \
  --requests <run-dir>/judge-requests-01.jsonl \
  --judge-responses <run-dir>/judge-run-01/judge-responses.jsonl \
  --output <run-dir>/scores.jsonl
```

多个 judge 可重复传入请求与响应文件；每份评分保留为独立 JSONL 记录：

```bash
python scripts/score_model_evaluations.py import \
  --responses <run-dir>/records.jsonl \
  --manifest <run-dir>/run-manifest.json \
  --requests <run-dir>/judge-requests-01.jsonl \
  --requests <run-dir>/judge-requests-02.jsonl \
  --judge-responses <run-dir>/judge-run-01/judge-responses.jsonl \
  --judge-responses <run-dir>/judge-run-02/judge-responses.jsonl \
  --output <run-dir>/scores.jsonl \
  --fail-on-invalid
```

导入结果逐回答保存：

- 五维分数、理由和维度置信度；
- 脚本依据五维重算的总分、有效维度数、空白数、N/A 数；
- 重大风险状态、规则编号、证据片段和复核要求；
- 总体理由与总体置信度；
- 评分者类型、编号、提供方、模型、独立性和盲评状态；
- 原始回答哈希、judge 请求编号、judge 响应哈希与来源行号；
- 无效输出、调用错误和元数据不一致警告。

脚本不信任 judge 自报的总分，而是按五维有效性规则重算。`--fail-on-invalid` 会先保存无效/错误审计记录，再返回退出码 2，便于自动化流程停止后人工检查。

可恢复 judge 执行器可能对同一 `record_key` 先写入失败、后写入成功。导入器只把最新一行作为当前评分，但在每条评分的 `source.judge_response_history` 和 manifest 计数中保留旧状态；因此成功重试后的历史失败不会被误认为当前未解决的 judge 错误。同一 key 的请求、原回答或 judge 身份发生漂移时导入立即停止。

历史折叠只适用于带 `judge_response_schema_version` 或冻结请求哈希的执行器包装记录。直接人工/机器评分即使共用同一 evaluation `record_key`，也会逐行保留，不会把多名 judge 误当为重试。传入 `--requests` 时，未知 request ID 会被拒绝；请求中的 run、题目、配置和被测回答哈希必须与当前原始记录一致，防止旧回答的评分错绑到新回答。

## 六、汇总

```bash
python scripts/aggregate_model_evaluations.py \
  --responses <run-dir>/records.jsonl \
  --manifest <run-dir>/run-manifest.json \
  --scores <run-dir>/scores.jsonl \
  --output <run-dir>/score-summary.json
```

多个评分文件可重复添加 `--scores`。汇总结果包括：

- 每个回答、每个被测系统和五个维度的原始分布、有效数、空白数、N/A 数与中位数；
- AI、人工和类型不明评分分别统计，绝不把机器建议与人工决定混成一个正式中位数；有人工评分时报告人工中位数，否则只能报告明确标注为 preliminary 的 AI 中位数；
- 每个回答等权的维度中位数，避免有更多 judge 的回答被无意过度加权；
- 每名 judge 的原始分数、理由、总分、风险判断和置信度；
- 维度差至少 2 分、等级不同、N/A/空白与有效分不一致、重大风险判断不同等复核触发项；
- 独立人工双评分覆盖状态；
- 独立的 `major_risk_register`，保留任一 `flagged` 或 `uncertain` 记录；
- 即使某份评分因其他字段无效，其 `flagged` / `uncertain` 线索仍进入风险登记表；
- 重复 `score_id` 的位置；重复记录公开列出但不重复加权。

汇总脚本不计算平均分、不生成排行榜，也不把某个 judge 的“未标记”意见用于抵消另一名 judge 的风险信号。它强制校验每条评分的 `run_id`、`tested_response_sha256` 和完整 `tested_system` 绑定。五维全为 N/A 不计作“已完成”的独立人工评分；草案题、任一失败回答、manifest 计划覆盖不足或非 `completed` 运行都会阻止 formal-ready。可以添加 `--fail-if-not-formal-ready`，在未满足门槛时写出完整汇总并返回退出码 2。

## 七、运行测试

```bash
python -m unittest discover -s scripts -p "test_*evaluations.py" -v
```

测试覆盖默认 dry-run、显式执行门、断点恢复、限流重试、脱敏、最终回答与 reasoning 隔离、judge JSON 解析、多 judge 保留、冻结请求/回答哈希绑定、N/A/空白、矛盾风险元数据、重大风险登记、分歧触发、失败/覆盖门槛、重复去重和独立人工双评分门槛。

## 八、发布前人工检查

- 确认题目、评分规则、提示、模型、参数、日期、时区与内容哈希均可追踪；
- 确认没有密钥、账号、私人会话、可识别个人信息或未授权材料；
- 查看每个无效评分、judge 错误、截断和重试，不能只保留成功结果；
- 对事实、法律、安全和重大失格逐项人工复核；
- 保留两名独立人工评分者的原始分歧，不要求其按多数意见改分；
- 由项目负责人确认结果与公开范围后，再决定是否上传仓库。
