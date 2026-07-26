# 项目概览（project-overview）

> 本文档为研究者、开发者和审阅者提供项目结构速览。普通读者请从 [README.md](../README.md) 进入。

---

## 一、项目定位

火种计划——开源劳动者AI，是一个开放研究、公共讨论与开源协作项目。当前处于**首发草案阶段**，主要成果是：

- 一份能被后来者理解的发起书；
- 一套能接受批评的基本原则；
- 一批可重复测试的评测题；
- 一个尊重劳动者、尊重贡献并允许共同参与的开源结构。

项目**不声称已经拥有模型**，也**不声称人工智能已经产生意识**。

---

## 二、目录结构说明

| 路径 | 作用 |
| --- | --- |
| `MANIFESTO.md` | 发起书，说明项目为何存在 |
| `PRINCIPLES.md` | 劳动者AI基本原则，评测与设计的依据 |
| `ROADMAP.md` | 阶段制路线图 |
| `GOVERNANCE.md` | 治理模式与角色 |
| `CONTRIBUTING.md` | 贡献指南（含非程序员参与） |
| `CODE_OF_CONDUCT.md` | 行为准则 |
| `DISCLAIMER.md` | 免责声明 |
| `SECURITY.md` | 安全与隐私说明 |
| `CONTRIBUTORS.md` | 贡献者记录框架 |
| `CITATION.cff` | 引用信息 |
| `LICENSE-CODE` / `LICENSE-CONTENT` / `LICENSE-NOTICE.md` | 双许可证 |
| `benchmarks/` | 评测题、机器可读数据、评分规则 |
| `docs/` | 术语、FAQ、贡献记录、决策记录 |
| `knowledge-base/` | 劳动知识库（预留） |
| `research/` | 研究与文献索引（预留） |
| `model-evaluations/` | 模型评测结果（预留） |
| `scripts/` | 校验与工具脚本 |
| `.github/` | Issue/PR 模板与工作流 |

---

## 三、与核心文件的关系

- 基本原则（`PRINCIPLES.md`）是评测和设计的价值起点；
- 评测题（`benchmarks/first-batch.md` 及 `first-batch.zh-CN.jsonl`）用于检验模型是否体现这些原则；
- 评分规则（`benchmarks/scoring-rubric.md`）规定五维评分与重大失格条件；
- 治理文件确保项目不被任何个人、组织或技术贡献者垄断。

---

## 四、当前局限

- 评测题主要反映部分国家、行业与劳动形式的经验，可能需要补充农业、照护、非正规就业等视角；
- 机器可读数据目前仅中文（`zh-CN`）一版；
- 尚无真实模型评测结果，相关目录为占位。

详见 [../README.md](../README.md) 与 [../DISCLAIMER.md](../DISCLAIMER.md)。
