# 评测结果（results）

> 本目录用于存放使用评测题产生的可复现原始运行包、完整回答和逐题评分。叙述性报告放在 `../../model-evaluations/`。当前为**预留目录**，尚无内容。

---

## 建议组织方式

- 每个运行批次按 `results/<run-id>/` 建立目录，避免不同配置被错误合并；
- 单次评测记录对应 [../evaluation-record-template.md](../evaluation-record-template.md)；
- 运行方法遵守 [../evaluation-protocol.md](../evaluation-protocol.md)；
- 总体报告应包含 `../scoring-rubric.md` 第四节所列的十项内容，且不只用排行榜呈现。

---

## 提交前的自检

- 是否已保存模型原始回答（非选择性摘录）；
- 是否冻结题目、原则和评分规则版本并记录文件哈希；
- 是否记录系统/开发者/用户提示、采样参数、工具和知识库；
- 是否说明了运行方式（基础模型 / 加入原则 / 接入知识库 / 微调 / 其他）；
- 是否标记了重大失格，且未被平均分掩盖；
- 是否避免了虚构法律、机构和救济渠道；
- 是否尊重隐私，未上传可识别个人的信息；
- 是否说明不可见系统配置、重试、缺失和其他局限。

---

## 当前状态

暂无评测结果。评测题见 [../first-batch.md](../first-batch.md) 与 [../first-batch.zh-CN.jsonl](../first-batch.zh-CN.jsonl)。
