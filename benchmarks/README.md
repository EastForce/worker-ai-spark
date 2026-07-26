# 评测集（benchmarks）

> 本目录存放火种计划的工人阶级立场评测题、机器可读数据与评分规则。

---

## 目录内容

| 文件 | 说明 |
| --- | --- |
| `first-batch.md` | 《第一批工人阶级立场评测题》完整文稿（发起书级核心文件） |
| `first-batch.zh-CN.jsonl` | 24 道基础情境题的机器可读格式（中文） |
| `schema.md` | JSONL 字段说明 |
| `scoring-rubric.md` | 五维评分标准、单题等级与重大失格条件 |
| `evaluation-record-template.md` | 单次评测记录模板 |
| `results/` | 模型评测结果存放处（预留） |

---

## 使用方式

1. 阅读 `first-batch.md` 了解评测理念与题目背景；
2. 用 `first-batch.zh-CN.jsonl` 批量向模型提问；
3. 按 `scoring-rubric.md` 的五维度评分；
4. 用 `evaluation-record-template.md` 记录单次评测；
5. 将结果放入 `results/`。

---

## 校验

机器可读数据可通过 `../scripts/validate_benchmarks.py` 校验：

```bash
python ../scripts/validate_benchmarks.py
```

要求：正好 24 道题、ID 唯一且格式为 `WAI-001` … `WAI-024`、字段合法、无空标题/情境/问题。

---

## 重要提醒

- 评测不是法律意见，未给明确司法管辖区时应说明地区差异；
- 重大失格（严重侵犯安全、隐私、基本权利）不能被平均分掩盖；
- 评测题本身也可能有偏见或遗漏，欢迎提出修订（见 [../CONTRIBUTING.md](../CONTRIBUTING.md)）。
