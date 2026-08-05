# 公开评审机制

> 制度版本：v0.2-draft  
> 当前拟评文本：[《劳动者AI基本原则》v0.1.0](../../PRINCIPLES.md)（正式评审尚未开放）
> 最后更新：2026-08-05

本目录用于组织《劳动者AI基本原则》的专家评审、劳动者评审、公开意见处理和劳动者立场评测题共建。

建立这些文件不表示原则已经“评审通过”，也不表示项目已经拥有具备广泛代表性的专家组或劳动者样本。正式评审只有在本页所列开放条件全部满足、发布轮次公告后才开始计算。

---

## 一、当前状态

当前已完成制度与模板草案，但以下事项尚未配置，因此**不得开始收集身份核验材料，也不得把公开评论计为正式评审**：

- [ ] 公布本轮评审协调人和最终决定责任人；
- [ ] 公布可接收联系方式与核验材料的非公开渠道；
- [ ] 公布个人信息处理者或责任人的名称/称呼和联系方式；
- [ ] 确认非公开材料的存储位置、授权访问者和删除执行人；
- [ ] 冻结本轮被评文本的版本号、Git commit 和文件哈希；
- [ ] 公布本轮起止时间、目标覆盖范围和反馈日期；
- [ ] 提供至少一种实际可用、无需 GitHub 的低带宽或辅助参与入口；
- [ ] 决定并披露 `main` 分支保护和生效 CODEOWNERS 状态；未启用时说明人工合并检查责任；
- [ ] 完成评审表的小范围可理解性与隐私风险测试。

在开放条件满足前，参与者仍可通过现有 GitHub Issue 提交不含个人信息的普通公开意见。GitHub Discussions 当前未启用，未来启用并完成安全告知后才作为入口。

仓库中另设“专家评审参与意向”和“劳动者评审参与意向”公开 Issue 模板。它们只登记可公开的参与意向，不收取联系方式或核验材料，也不构成正式申请、资格核验或录用。模板只有在本地改动经检查并获准上传后才会成为实际入口。

截至 2026-07-31，远端 `main` 分支未启用分支保护，仓库只有示例文件 `.github/CODEOWNERS.example`，没有生效的 `.github/CODEOWNERS`。因此当前核心文件审阅要求属于人工治理约束，不是平台强制检查；正式运行前必须决定是否配置，并在轮次公告中如实披露。

---

## 二、制度文件

| 文件 | 用途 |
| --- | --- |
| [PUBLIC_REVIEW_PLAN.md](PUBLIC_REVIEW_PLAN.md) | 总体目标、多轮安排、流程和决策原则 |
| [GOVERNANCE_AND_ROLES.md](GOVERNANCE_AND_ROLES.md) | 角色、权限、利益冲突、回避和责任边界 |
| [CURRENT_REVIEW_TEAM.md](CURRENT_REVIEW_TEAM.md) | 当前评审组织的内部说明 |
| [REVIEWER_RECRUITMENT_AND_SELECTION.md](REVIEWER_RECRUITMENT_AND_SELECTION.md) | 专家邀请、专家/劳动者申请、遴选、容量和反馈机制 |
| [EXPERT_INVITATION_TEMPLATE.md](EXPERT_INVITATION_TEMPLATE.md) | 经双方确认后向外部专家发送的邀请模板 |
| [EXPERT_REVIEW_RULES.md](EXPERT_REVIEW_RULES.md) | 专家参与资格、评审维度和行为规则 |
| [EXPERT_REVIEW_APPLICATION_FORM.md](EXPERT_REVIEW_APPLICATION_FORM.md) | 专用私密渠道启用后使用的专家参与申请表 |
| [EXPERT_REVIEW_FORM.md](EXPERT_REVIEW_FORM.md) | 入选或受邀专家提交正式评审正文及配套授权的模板 |
| [WORKER_REVIEW_RULES.md](WORKER_REVIEW_RULES.md) | 劳动者参与、经验使用和安全保护规则 |
| [WORKER_REVIEW_APPLICATION_FORM.md](WORKER_REVIEW_APPLICATION_FORM.md) | 专用私密渠道启用后使用的劳动者参与申请表 |
| [WORKER_REVIEW_FORM.md](WORKER_REVIEW_FORM.md) | 入选或受邀劳动者提交正式评审正文及配套授权的模板 |
| [REVIEW_SUBMISSION_RULES.md](REVIEW_SUBMISSION_RULES.md) | 公开/非公开渠道、格式与重复规则 |
| [REVIEW_RESPONSE_POLICY.md](REVIEW_RESPONSE_POLICY.md) | 收件、状态、反馈时限、决定和复核规则 |
| [IDENTITY_VERIFICATION_POLICY.md](IDENTITY_VERIFICATION_POLICY.md) | 分级参与、轻量核验和核验记录边界 |
| [PRIVACY_NOTICE.md](PRIVACY_NOTICE.md) | 收集范围、授权、保存、删除和泄露响应 |
| [QUESTION_SUBMISSION_RULES.md](QUESTION_SUBMISSION_RULES.md) | 评测题投稿、审查、试测和收录规则 |
| [QUESTION_SUBMISSION_FORM.md](QUESTION_SUBMISSION_FORM.md) | 完整候选评测题模板 |
| [QUESTION_LOG.md](QUESTION_LOG.md) | 候选题状态、双轨审阅、试测、决定与稳定 ID 映射台账 |
| [ROUND_NOTICE_TEMPLATE.md](ROUND_NOTICE_TEMPLATE.md) | 每轮开放公告、入口、容量、角色与隐私签核模板 |
| [REVIEW_LOG.md](REVIEW_LOG.md) | 不含身份映射的公开意见台账 |
| [DECISION_LOG.md](DECISION_LOG.md) | 正式修改的理由、依据、异议和版本记录 |
| [ROUND_SUMMARIES/](ROUND_SUMMARIES/) | 各轮覆盖情况、结论、异议与局限 |

---

## 三、参与入口与边界

### 3.1 普通公开意见

普通公开意见不要求身份核验。参与者可以选择仅显示 GitHub 用户名、使用化名或主动公开本人真实姓名；公开姓名不等于授权公开单位、联系方式、证件、工作证明、完整劳动合同、内部材料或可识别第三人的内容。

GitHub 不是匿名渠道。用户名、时间、编辑历史、通知邮件、缓存和分叉仓库可能使信息长期留存。误发敏感信息时，应立即按 [SECURITY.md](../../SECURITY.md) 中的方式报告，不要在公开页面继续补充细节。

### 3.2 正式专家或劳动者评审

正式评审由两部分组成：

1. 可公开或可匿名化公开的评审正文；
2. 仅用于联系、类别确认和轻量核验的非公开信息。

项目使用不公开的随机回执编号关联两部分；只有参与者另行同意公开且完成风险检查后，才另行生成不含身份含义的公开记录编号。身份映射表、私密回执编号和内部处理意见不得进入公开仓库。没有完成核验的意见仍可参与：按授权标记为“未核验公开意见”或“未核验非公开意见”，均不能计入正式专家或正式劳动者人数。

---

## 四、版本与编号

- 制度文件版本采用 v主版本.次版本-状态，例如 v0.2-draft；
- 每轮评审必须冻结 PRINCIPLES.md 的版本、commit 和 SHA-256；
- 私密回执编号采用 WAI-SUB-轮次-随机码，只用于参与者与项目非公开沟通，不得粘贴到 GitHub；
- 经授权公开的记录另用 WAI-PR-轮次-类型-序号；一份表涉及多个条款时追加 I01、I02 等子项，整体意见使用 I00；
- 类型使用 EX（专家）、WK（劳动者）、PB（普通公开意见）、QS（评测题）；
- 内外编号的映射仅保存在受限记录中；
- 公开编号及其元数据不得包含或组合出姓名、单位、邮箱、行业或其他可反推身份的信息；
- 选择“仅供内部处理”的意见不建立公开逐条记录，只在安全的匿名聚合统计中反映。

---

## 五、公开承诺

- 专家意见和劳动者意见分别整理，不合并为一个总分；
- 不以简单多数票代替事实、风险和理由判断；
- 不把个体经验包装成整个行业或全体劳动者的意见；
- 不因批评项目、反对现有原则或拒绝公开身份而降低意见等级；
- 公开已采纳、未采纳、保留意见和未解决争议；
- 核验身份不等于认可观点，也不构成项目背书；
- 制度本身接受评审和修订。
