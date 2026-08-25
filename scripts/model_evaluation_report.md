# 模型评测中文 Markdown 报告生成器

`build_model_evaluation_report.py` 把一个或多个本地模型运行、评分 JSONL 或评分汇总 JSON 整理成 answer-first 的中文技术报告。脚本完全离线：不读取密钥文件、不访问网络，也不执行模型调用。

## 输入与运行

标准 run 目录包含 `records.jsonl` 和 `run-manifest.json`。可重复传入多个目录：

```bash
python scripts/build_model_evaluation_report.py \
  --run-dir benchmarks/results/_pending/run-a \
  --run-dir benchmarks/results/_pending/run-b \
  --scores benchmarks/results/_pending/run-a/scores-judge-1.jsonl \
  --scores benchmarks/results/_pending/run-a/scores-judge-2.jsonl \
  --aggregation benchmarks/results/_pending/run-b/score-summary.json \
  --output benchmarks/results/_pending/evaluation-report.zh-CN.md
```

也可用成对的 `--responses` / `--manifest`：

```bash
python scripts/build_model_evaluation_report.py \
  --responses path/to/run-a/records.jsonl \
  --manifest path/to/run-a/run-manifest.json \
  --responses path/to/run-b/records.jsonl \
  --manifest path/to/run-b/run-manifest.json \
  --output path/to/report.md
```

省略 `--manifest` 时，脚本会查找每个 `records.jsonl` 同目录的 `run-manifest.json`。评分 JSONL 按 `run_id` 匹配；已有 aggregation 优先，并校验其 `responses_sha256`（以及可用时的 manifest 哈希），避免旧评分错绑到新回答。已有输出默认不覆盖，只有显式添加 `--overwrite` 才允许覆盖。

## 报告边界

- 覆盖率从每个 `record_key` 的最新记录重算，manifest 计数只作交叉核验；
- API 鉴权、配额/限流、传输、截断与计划记录缺失分别计数，不换算成低分；
- 完整 24 题与部分覆盖模型分开，部分覆盖分数不进入横向比较；
- requested model 与 `returned_model` 同时展示；多个请求名折叠到同一 returned model 时发出 alias/version-collapse 警告，但仍保留各请求配置的记录；
- AI judge 初评和人工评分分别报告；AI 不计入至少两名独立人工评分者的正式门槛；
- invalid、judge error、`flagged`、`uncertain` 与评分分歧逐项登记，不被中位数抵消；
- 只使用回答等权中位数，不计算平均分、不生成排行榜；
- smoke、probe、quota-recheck、catalog 等诊断运行默认排除。只有研究诊断本身时才显式添加 `--include-diagnostics`，且它们仍标记为不可比较。

报告不展开提示正文，只保存提示对象哈希；生成参数中的敏感键会被脱敏。输入快照时间来自 manifest，因此相同输入、相同脚本和相同参数会生成稳定内容。

## 离线回归

```bash
python -X utf8 -B scripts/test_build_model_evaluation_report.py -v
```

测试使用临时合成数据，不读取凭据，也不请求任何 API。
