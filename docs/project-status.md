# 当前进展与参与入口

> 状态核对日期：2026-09-06。平台状态可能变化；本页区分已经具备的成果、现在可以参与的事项和仍在筹备的工作。

**普通意见征集已开放；正式评审尚未开放。** 第一次来，可以先看 [用三道题认识劳动者AI](start-here.md)，选择一道题留下一条意见，无需会编程或先读完全部理论文件。

## 已经有什么

| 内容 | 当前状态与材料 |
| --- | --- |
| 项目主张与理论 | 已有发起书、基本原则、治理框架和 [两篇母理论公开讨论稿](../theory/papers/README.md)，仍可批评和修订；不代表劳动者、专家或机构已经形成共同意见。 |
| 评测题 | 已有 [24 道劳动情境题](../benchmarks/first-batch.md) 及机器可读数据。 |
| 模型试测 | 已有 [DeepSeek 24 题 pilot 待审包](../benchmarks/results/wai-20260825-deepseek-pilot/)，保存了三个请求模型各答 24 题的 72 条回答。已有 AI 初评，人工评分为 0，尚无独立人工双评；不构成正式排名或评审通过。 |
| 专属模型 | 尚未训练专属模型；本地开源原型仍是未来设想。 |
| 正式评审 | 制度和模板已有草案，开放条件尚未全部落实，见 [评审机制](review/README.md#一当前状态)。 |

## 现在怎样参与

- **只提一条意见：** 在 [GitHub Discussions](https://github.com/EastForce/worker-ai-spark/discussions) 写出对应的题号或段落，以及你赞同、反对或认为遗漏的内容。可以只写几句话，不需要提交个人经历或证明身份。
- **指出具体错误或提出修改：** 使用仓库现有 [Issue 入口](https://github.com/EastForce/worker-ai-spark/issues/new/choose)，也可以提交 Pull Request。完整规则见 [贡献指南](../CONTRIBUTING.md)。
- **不便使用 GitHub：** 将简短意见或去标识的初步说明发送到 [worker.ai.spark@gmail.com](mailto:worker.ai.spark@gmail.com)。维护方可以看到发件地址；邮件不是匿名或端到端加密渠道。邮件公开前须另行确认最终文本、署名方式和许可。

公开留言会显示 GitHub 账号；请只提交适合公开的内容。不要公开证件、联系方式、完整合同、工资资料或可识别第三人的材料。邮件也不适合直接发送这些敏感原件；具体要求见 [邮箱投稿说明](../CONTRIBUTING.md#四邮箱投稿)。

**意见怎样处理：** 普通意见不计为正式评审，也不表示提交者支持整个项目。实际采纳、修改或暂未采纳的理由，应随相关讨论或版本修改说明记录；收到意见不等于已经采纳。目前不承诺逐条回复或固定回复期限。

## 正式评审何时开始

在责任人、材料处理安排、被评文本版本、轮次日期和实际参与入口等条件落实后，项目才会发布正式轮次公告。此前不收集正式评审身份核验材料，不将普通评论记为正式评审结果。

已有的 [公开邀请帖](https://github.com/EastForce/worker-ai-spark/discussions/4) 可以用于提出普通意见；邀请帖本身不证明正式轮次已经启动。是否启动应以 [开放条件](review/README.md#一当前状态) 全部落实及正式轮次公告为准。

## 内容版本与平台发布

文件内的内容版本标记、Git tag 和 GitHub Release 是不同记录，不应相互替代。

- 当前仓库内容标记为 `v0.1.0`；引用具体内容时，仍应注明文件路径及相应 commit。
- 2026-09-06 核对公开上游仓库：已有 Git tag [`v0.1-draft`](https://github.com/EastForce/worker-ai-spark/tree/v0.1-draft)，指向 commit `f36f72d21254f4b04b9a0314a2261010ecca3e58`；它不等于文件内容版本 `v0.1.0`。
- 同日 [GitHub Releases](https://github.com/EastForce/worker-ai-spark/releases) 尚无已发布条目。

后续状态变化时，应更新本页核对日期与对应证据。阶段目标见 [路线图](../ROADMAP.md)。
