# 变更日志（CHANGELOG）

> 本文件记录项目主要版本与文件变化。重大修改应保留历史版本，并接受公开讨论。

---

## 未发布：评审机制与评测工具调整（等待项目发起人检查）

- 评审机制继续在本地筹备，具体规则暂不对外介绍；
- 明确劳动者可以提交本人亲历、直接了解或有合理事实依据的真实案例，并选择实名、化名、匿名或仅供内部处理；
- 完善评测记录、数据校验和自动测试。

---

## v0.1-draft（2026-07-26 审批通过）

发起人已就首发阶段五项事项作出决定，本版本据此定稿：

- **许可证批准**：代码 Apache-2.0 + 内容 CC BY-SA 4.0 正式生效（见 LICENSE-NOTICE.md、README.md、CITATION.cff）；
- **安全联系方式**：新增安全邮箱 **worker.ai.spark@gmail.com**（见 SECURITY.md）；
- **Discussions 状态更正**：v0.1 记录曾写为“已开放”；截至 2026-07-31 远端实际未启用，当前入口说明已据实更正，未来启用时另行记录；
- **真实经历提交**：劳动者可以选择实名、化名、匿名或仅供内部处理；身份、单位、联系方式、原始材料和正文分别授权，对高风险材料实行去标识化或暂缓公开；
- **发布标签**：发布至 GitHub 时打 `v0.1-draft` 标签（由发起人手动发布，本仓库未自动推送）。

---

## v0.1-draft（2026-07-26 更新：发起人信息）

- 明确项目发起人署名为“Worker AI Spark发起人”；
- 在 GOVERNANCE.md、README.md、CONTRIBUTORS.md、CITATION.cff、docs/contribution-records.md 补充发起人身份与披露政策；
- 首发阶段不公开现实工作单位与完整个人信息；真实身份可在必要的专业审阅、合作与责任核实时，向可信参与者适度披露；
- 发起人承担初始内容责任，但不主张永久垄断工人阶级立场的解释权。

---

## v0.1-draft（首发草案）

- 建立 `worker-ai-spark` 仓库基础结构；
- 纳入三份核心文件：
  - `MANIFESTO.md`（《工人阶级立场AI火种计划发起书》）；
  - `PRINCIPLES.md`（《劳动者AI基本原则》）；
  - `benchmarks/first-batch.md`（《第一批工人阶级立场评测题》）；
- 生成配套文件：README、ROADMAP、GOVERNANCE、CONTRIBUTING、CODE_OF_CONDUCT、DISCLAIMER、SECURITY、CONTRIBUTORS、CITATION.cff、许可证文件；
- 生成评测支持文件：`benchmarks/schema.md`、`benchmarks/scoring-rubric.md`、`benchmarks/evaluation-record-template.md`；
- 将 24 道基础评测题转换为机器可读格式：`benchmarks/first-batch.zh-CN.jsonl`；
- 建立基础校验工具：`scripts/validate_benchmarks.py` 与 GitHub Actions 工作流；
- 建立 Issue 模板、Pull Request 模板与 CODEOWNERS 示例；
- 预留目录：docs、knowledge-base、research、model-evaluations。

> 说明：本版本为草案，内容与许可证均需发起人最终确认。不声称已拥有模型或已证明人工智能具有意识。
