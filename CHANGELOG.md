# 变更日志（CHANGELOG）

> 本文件记录项目主要版本与文件变化。重大修改应保留历史版本，并接受公开讨论。

---

## 论文体系的建立与迭代

- 2026-08-25：收录 `MT-00`《人工智能“以人为本”何以现实化——劳动关系中的目标—后果错位与劳动者立场》和 `MT-01`《劳动者何以成为人工智能治理主体——权利、能力、发言与救济的规范构型》的中英文完整 Markdown 公开讨论稿，版本均为 `v0.1.0`；
- 上述论文仍处于“文献研究—母理论提出”阶段，尚未经过项目实践验证或正式同行评议；`MT-02`—`MT-11` 仍在筹备中，尚无公开全文；
- 增加官方邮箱投稿渠道 `worker.ai.spark@gmail.com`，用于接收理论论文及不宜直接在 GitHub 公开的初步材料；同步明确邮件不保证匿名或端到端加密，未经作者确认最终文本、公开署名方式和许可不得公开；
- 建立母理论与核心论文的双语公开入口，明确当前处于“文献研究—母理论提出”阶段；
- 经项目确认收录的贡献，构成项目对作者在本项目理论建设中所作贡献的公开认可，并可根据作者意愿在项目仓库和官网予以致谢。该认可仅表示相关文本或其部分被纳入项目的理论讨论与版本记录，不代表其观点已经被证明正确、经过实践验证或不可修订；亦不构成期刊发表、正式同行评议、学术资格认证或任何经济回报承诺。
- GitHub Discussions 已启用，普通公开意见和理论提议可以通过 Discussions 或 Issue 提交；正式评审仍须满足评审机制规定的开放条件。

---

## v0.1.0（2026-08-05）

- 发布首个正式版本，统一发起书、基本原则、首批评测题和引用元数据的版本标记；
- 纳入评审制度与模板草案，并明确正式评审尚未开放；
- 明确劳动者可以提交本人亲历、直接了解或有合理事实依据的真实案例，并选择实名、化名、匿名或仅供内部处理；
- 完善评测记录、数据校验和自动测试；
- 统一隐私与安全报告入口。

---

## v0.1-draft（2026-07-26 审批通过）

发起人已就首发阶段五项事项作出决定，本版本据此定稿：

- **许可证批准**：代码 Apache-2.0 + 内容 CC BY-SA 4.0 正式生效（见 LICENSE-NOTICE.md、README.md、CITATION.cff）；
- **安全联系方式**：新增安全邮箱 **worker.ai.spark@gmail.com**（见 SECURITY.md）；
- **Discussions 历史状态**：该功能现已启用；
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
