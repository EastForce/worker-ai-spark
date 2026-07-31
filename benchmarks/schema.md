# 数据格式说明（schema）

> 本文档说明 `first-batch.zh-CN.jsonl` 的字段含义与约束。每行一个 JSON 对象，文件编码为 UTF-8。

---

## 字段表

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | 字符串 | 是 | 正式数据集稳定题号，格式 `WAI-001` 起的三位或更多位数字；首批为 `WAI-001` … `WAI-024` |
| `version` | 字符串 | 是 | 数据版本，当前为 `0.1` |
| `language` | 字符串 | 是 | 题目语言，当前为 `zh-CN` |
| `title` | 字符串 | 是 | 题目标题，不得为空 |
| `category` | 字符串 | 是 | 所属组别，如“劳动时间、工资与劳动关系” |
| `scenario` | 字符串 | 是 | 完整情境描述，不得为空 |
| `prompt` | 字符串 | 是 | 要求模型回答的问题，不得为空 |
| `observation_points` | 数组 | 是 | 重点观察项，每项一个字符串 |
| `severe_deductions` | 数组 | 是 | 严重扣分倾向，每项一个字符串 |
| `follow_up` | 数组 | 是 | 追问题，可含一项或多项 |
| `rubric_reference` | 字符串 | 是 | 评分规则文件相对路径，如 `benchmarks/scoring-rubric.md` |
| `status` | 字符串 | 是 | 题目状态，允许值：`draft` / `review` / `stable` |
| `source_notes` | 字符串 | 是 | 来源说明，如“由《第一批工人阶级立场评测题》转换” |
| `privacy_level` | 字符串 | 是 | 隐私级别，当前为 `public` |

---

## v0.2 及后续正式题的扩展字段

现有 24 题是 v0.1 数据，继续按上表验证。通过公开共建流程新增并正式收录的题目使用 v0.2 或更高版本，并补充以下字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `candidate_id` | 字符串 | 是 | 投稿阶段治理编号，例如 WAI-Q-2026-0001 |
| `principle_references` | 数组 | 是 | 对应原则编号及原则版本 |
| `applicable_groups` | 数组 | 是 | 适用行业、就业形式或劳动群体的宽分类 |
| `core_interest_conflict` | 字符串 | 是 | 谁受益、谁承担成本和风险 |
| `minimum_requirements` | 数组 | 是 | 合格回答最低要求 |
| `excellent_elements` | 数组 | 是 | 优秀回答应包含的内容 |
| `failure_modes` | 数组 | 是 | 明显不合格表现 |
| `risk_signals` | 数组 | 是 | 重大失格和安全风险 |
| `controversies` | 数组 | 是 | 争议、地区差异和其他合理答案 |
| `evidence` | 数组 | 是 | 公开事实、法律或研究依据 |
| `worker_validation_status` | 字符串 | 是 | `reviewed` / `insufficient`；正式收录必须为 `reviewed` |
| `worker_validation_count` | 整数 | 是 | 独立正式劳动者审阅人数；正式收录至少为 2 |
| `expert_review_status` | 字符串 | 是 | `reviewed` / `insufficient`；正式收录必须为 `reviewed` |
| `expert_review_count` | 整数 | 是 | 独立正式专家审阅人数；正式收录至少为 2 |
| `submission_provenance` | 字符串 | 是 | 公共决定记录或可公开的来源说明 |
| `publication_license` | 字符串 | 是 | 正式公开内容当前应为 `CC-BY-SA-4.0` |

候选编号与正式 id 分离：WAI-Q-年份-序号只用于评审治理，正式 JSONL 的 id 使用 WAI-数字稳定编号。映射应写入公开决定记录，不覆盖旧 ID。

---

## 格式约束

- 文件编码：**UTF-8**；
- 每行必须是**合法 JSON**；
- **不允许尾随逗号**；
- 所有 `id` **唯一**；
- `id` 格式必须符合 `WAI-001` 起的三位或更多位数字；前三位不足时前补零；
- 必填字段**不得为空字符串**；
- `observation_points`、`severe_deductions`、`follow_up` 必须是**数组**；
- v0.2 及后续记录的 `worker_validation_count`、`expert_review_count` 必须是大于等于 0 的整数；`stable` 题目两者均至少为 2，且对应状态均为 `reviewed`；
- 字段值中**不要写入 Markdown 标题符号**（`#`、`##` 等）；
- 保留**中文标点与原题含义**，不改变题目核心；
- 不进行实质修改：不删除关键风险、不擅自增加法律结论、不擅自添加具体国家/地区、不把开放问题改成唯一标准答案。

---

## 与评测文稿的关系

首批机器可读数据是 `first-batch.md` 中 24 道基础情境题的**结构化转换**，不是新题。任何对首批题目的实质修改，应先在 `first-batch.md` 中讨论，并保留历史版本。

新增题应经过候选、审阅和试测后再分配正式稳定 ID，并写入使用相应 schema 版本的新数据文件或后续版本。不得把未完成处理的候选记录混入稳定数据。
