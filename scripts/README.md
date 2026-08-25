# 脚本（scripts）

> 本目录存放项目使用的脚本。当前仅依赖 Python 标准库，不引入第三方包。

> 下列命令均从仓库根目录运行。

---

## validate_benchmarks.py

校验 `../benchmarks/first-batch.zh-CN.jsonl` 的基本结构与内容。

**依赖**：仅 Python 标准库（无需 `pip install`）。

**运行**：

```bash
python scripts/validate_benchmarks.py
```

也可显式指定文件路径：

```bash
python scripts/validate_benchmarks.py benchmarks/first-batch.zh-CN.jsonl
```

**检查项**：

- JSONL 每行可解析；
- 正好包含 24 道首批题目；
- ID 唯一且格式为 `WAI-001` … `WAI-024`；
- 必填字段齐全；
- 数组字段（`observation_points`、`severe_deductions`、`follow_up`）类型正确且非空；
- `language` 为 `zh-CN`；
- `status` 为允许值（`draft` / `review` / `stable`）；
- 标题、情境、问题均不为空。

**输出**：

- 成功：`Benchmark validation passed.` / `Total cases: 24` / `Errors: 0`；
- 失败：输出具体行号、题目 ID、原因，并以非零退出码结束。

该脚本也由 `.github/workflows/validate-benchmarks.yml` 在每次 push 与 Pull Request 时自动运行。

---

## validate_benchmark_sources.py

只读核对 `benchmarks/first-batch.md` 与
`benchmarks/first-batch.zh-CN.jsonl` 的 24 道基础题字段：

```bash
python scripts/validate_benchmark_sources.py
```

标题、分组、情境、主问题、重点观察或严重扣分倾向发生漂移时，脚本以非零状态退出。追问不会被自动改写，差异会逐题报告为待审阅警告。

该脚本及其测试也已纳入 `.github/workflows/validate-benchmarks.yml`。

标准库测试：

```bash
python -B scripts/test_validate_benchmark_sources.py
```

---

## run_model_evaluations.py

评测运行器仅在显式使用 `--execute` 时请求模型 API。MiniMax 默认使用
OpenAI 兼容 SSE 流式响应，以避免推理模型长时非流式连接中断：

```bash
python scripts/run_model_evaluations.py run \
  --provider minimax \
  --model minimax=MiniMax-M2.7 \
  --output-dir benchmarks/results/_pending/minimax-run \
  --execute
```

- `--minimax-stream` 是默认值；`--no-minimax-stream` 仅用于兼容性核对；
- 该开关只改变 MiniMax 传输，不改变 DeepSeek、火山引擎或 Gemini；
- 命令行的 `--max-tokens` 是统一抽象参数；MiniMax 请求会将其映射为
  `max_completion_tokens`；
- 成功记录的 `raw_response` 按顺序保存已脱敏 SSE chunk，并区分
  `[DONE]` 终止与 MiniMax “已终止 `finish_reason` + 干净 EOF”两种完成方式；
  无终止原因的 EOF 或异常断连仍是 partial error；
- 断流的 chunk 只属于当次错误/重试历史，不会与后续尝试合并或被记为
  成功；供应商未返回 usage 时保留空对象并设置 `usage_missing: true`；
- 合并流内容后，仅完整的 MiniMax `<think>...</think>` 块会被分离到
  `reasoning_text`，未闭合标签不会被静默删除。

Gemini 默认仍使用非流式 `generateContent`，长回答模型可显式切换到官方
`streamGenerateContent?alt=sse` JSON SSE 端点：

```bash
python scripts/run_model_evaluations.py run \
  --provider gemini \
  --model gemini=models/gemma-4-31b-it \
  --gemini-stream \
  --max-tokens 8192 \
  --output-dir benchmarks/results/_pending/gemma-stream-run \
  --execute
```

- `--gemini-stream` 仅改变 Gemini 传输；`--no-gemini-stream` 为默认值；
- API key 仍只通过 `x-goog-api-key` 请求头发送，不进入 URL 或持久化结果；
- `raw_response` 依线序保存已脱敏 JSON chunk、HTTP 状态、协议、干净 EOF
  终止方式与最终 `finishReason`；`modelVersion`、`usageMetadata`、thought parts
  和最终回答分别进入可审计字段；
- 只有主候选返回明确的终止 `finishReason` 且连接正常结束，流才可成功；异常
  断连、没有终止状态或无最终文本均为错误；`MAX_TOKENS` 继续按截断错误处理；
- `gemini_stream` 会进入运行 manifest、每条请求的有效参数和请求哈希，恢复
  执行时不得改变，以免把流式与非流式记录混入同一批次。

离线回归（不读取密钥、不发起网络请求）：

```bash
python -B scripts/test_run_model_evaluations.py
```

---

## run_judge_evaluations.py

AI 初评请求执行器默认 dry-run，只在显式使用 `--execute` 时调用
provider。DeepSeek 可用 `--deepseek-thinking enabled|disabled` 冻结思考模式；
该值只进入 DeepSeek 请求的 `thinking.type`，不传给其他 provider。

MiniMax judge 默认使用 `--minimax-stream`，可用 `--no-minimax-stream` 作兼容性
核对。这些 provider 专用开关会写入 manifest、有效参数和记录哈希；
`--resume` 要求开关与原批次一致。完整用法与审计边界见
[`model_evaluation_scoring_pipeline.md`](model_evaluation_scoring_pipeline.md)。

离线回归（不读取密钥、不发起网络请求）：

```bash
python -B scripts/test_run_judge_evaluations.py
```

---

## build_model_evaluation_report.py

把一个或多个本地 response run、评分 JSONL 或 aggregation JSON 生成中文
Markdown 技术报告。脚本离线重算覆盖率，分开显示 API 失败、完整 24 题与部分
模型、AI 初评与人工评分，并逐项保留无效评分、风险及分歧。requested model 与
`returned_model` 会同时显示；诊断运行默认不进入比较。

```bash
python scripts/build_model_evaluation_report.py \
  --run-dir benchmarks/results/_pending/run-a \
  --scores benchmarks/results/_pending/run-a/scores.jsonl \
  --output benchmarks/results/_pending/evaluation-report.zh-CN.md
```

完整输入约定与边界见
[`model_evaluation_report.md`](model_evaluation_report.md)。离线回归：

```bash
python -X utf8 -B scripts/test_build_model_evaluation_report.py -v
```
