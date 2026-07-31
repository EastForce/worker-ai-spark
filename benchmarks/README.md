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
| `evaluation-protocol.md` | 多模型运行、版本冻结、人工评分、复核和公开规则 |
| `evaluation-record-template.md` | 单次评测记录模板 |
| `results/` | 可复现的原始运行包和逐题评分存放处（预留） |

---

## 使用方式

1. 阅读 `first-batch.md` 了解评测理念与题目背景；
2. 用 `first-batch.zh-CN.jsonl` 批量向模型提问；
3. 按 `evaluation-protocol.md` 冻结版本、提示和运行参数；
4. 按 `scoring-rubric.md` 的五维度进行独立人工评分；
5. 用 `evaluation-record-template.md` 记录单次评测；
6. 将原始运行包放入 `results/`，将叙述报告放入 `../model-evaluations/`。

---

## 校验

机器可读数据可通过 `../scripts/validate_benchmarks.py` 校验：

```bash
python ../scripts/validate_benchmarks.py
```

默认校验首批文件时要求正好 24 道题、ID 唯一且为 `WAI-001` … `WAI-024`、字段合法、无空标题/情境/问题。

也可以把其他 JSONL 路径作为第一个参数传入。非首批文件不强制 24 题；v0.2 及后续记录还会检查 [schema.md](schema.md) 中的共建来源、原则关联、评分要素、劳动者/专家审阅状态与人数，以及公开许可字段。

校验脚本的标准库测试：

```bash
python -B scripts/test_validate_benchmarks.py
```

---

## 重要提醒

- 评测不是法律意见，未给明确司法管辖区时应说明地区差异；
- 重大失格（严重侵犯安全、隐私、基本权利）不能被平均分掩盖；
- 评测题本身也可能有偏见或遗漏，欢迎提出修订（见 [../CONTRIBUTING.md](../CONTRIBUTING.md)）。
