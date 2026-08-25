# Core Papers

> **Chinese version:** [阅读中文版](README.md)

This directory contains the public core papers of the Worker AI Spark Project's foundational theoretical framework. Papers will be added individually after meeting the basic requirements for a public discussion draft. A draft that is unfinished or has not passed pre-release checks will not be described as a completed theoretical output merely because it appears in the research plan.

---

## Current Status

This directory now includes the first two foundational-theory papers as complete Chinese and English Markdown public discussion drafts. They may be cited, criticized, and revised, but remain at the “literature research–theory formulation” stage. They have not been validated through project practice or formal peer review and do not represent a consensus among workers, trade unions, experts, or institutions.

| Identifier | Full title | Version and date | Public status | Full text |
| --- | --- | --- | --- | --- |
| `MT-00` | *Making the “Human-Centered” Principle in AI Substantive: The Goal–Consequence Mismatch in Labor Relations from a Workers’ Standpoint*<br>《人工智能“以人为本”何以现实化——劳动关系中的目标—后果错位与劳动者立场》 | `v0.1.0`<br>2026-08-25 | Public discussion draft | [Chinese Markdown](mt-00/paper.zh-CN.md) · [English Markdown](mt-00/paper.en.md) |
| `MT-01` | *How Can Workers Become Subjects of Artificial Intelligence Governance? The Normative Configuration of Rights, Capabilities, Voice, and Remedy*<br>《劳动者何以成为人工智能治理主体——权利、能力、发言与救济的规范构型》 | `v0.1.0`<br>2026-08-25 | Public discussion draft | [Chinese Markdown](mt-01/paper.zh-CN.md) · [English Markdown](mt-01/paper.en.md) |
| `MT-02`–`MT-11` | Remaining thematic foundational-theory papers | — | In preparation | No public full text |

`MT-00` is the general paper, defining the shared object of analysis, basic standpoint, and theoretical point of departure. `MT-01`–`MT-11` are thematic foundational-theory papers that develop questions concerning workers' status as subjects, mechanisms, rights, systems, governance, evaluation, boundaries, and responsibility. Apart from the released `MT-00` and `MT-01`, papers `MT-02`–`MT-11` remain in preparation and are not presented as completed project outputs.

Identifiers are repository indexing tools and do not need to appear as part of a paper's main title. Main titles should remain natural and complete academic titles.

---

## Paper Format

Core papers use a familiar academic form, generally including:

1. A title and, where needed, a subtitle;
2. A Chinese abstract and keywords;
3. An English title, abstract, and keywords;
4. Statement of the problem, review of relevant literature, conceptual clarification, and theoretical argument;
5. Major objections, competing explanations, and conditions of applicability;
6. Theoretical implications for the Worker AI Spark Project, without presenting future practice as empirical results;
7. A conclusion;
8. A complete reference list.

The project does not require papers to follow the length, section, or subject-matter limits of a particular journal. It also does not imitate a journal's name, branded layout, submission dates, or peer-review decisions. Explanatory footnotes, analytical figures, tables, and appendices may be used where they are genuinely needed, but report-style callout boxes and task lists should not replace continuous argument.

The project does not charge submission, review, or inclusion fees for theoretical contributions, and contributions need not be written by humans acting alone. Papers developed through human–AI collaboration may be submitted on equal terms, with the submitting person responsible for the content and its sources. “Inclusion” means entry into the project's public theoretical framework; it is not journal publication, formal peer review, or academic certification. See the [foundational theory overview](../README.en.md#open-theoretical-contributions) for contribution and acknowledgment principles.

A complete paper may be submitted publicly through a Pull Request or sent to the official mailbox, [worker.ai.spark@gmail.com](mailto:worker.ai.spark@gmail.com), with a subject such as “`[Theory Submission] Paper Title`.” An emailed manuscript must not enter the repository until the author explicitly confirms the final text, public attribution, and license. Email is neither anonymous nor end-to-end encrypted; high-risk or third-party material should first be de-identified and reduced to a minimal description. See the [email-submission procedure](../../CONTRIBUTING.md#四邮箱投稿) (currently in Chinese) for format and handling boundaries.

---

## Recommended Directory Structure

Once released, each paper should normally have its own subdirectory:

```text
papers/
└─ mt-00/
   ├─ README.md          # Abstract, version, status, citation, and file links
   ├─ paper.zh-CN.md     # Chinese text for repository search and version comparison
   ├─ paper.zh-CN.pdf    # Stable, paper-formatted Chinese reading version
   ├─ paper.en.md        # English text, when available
   └─ paper.en.pdf       # English reading version, when available
```

Word files may be retained as editing sources, but whether they should be public depends on privacy, metadata, and version-management considerations. Markdown supports review and change tracking, while PDF preserves stable layout and an academic reading experience. Files identified as the same version should contain substantively equivalent text.

---

## Versions and Status Labels

Each paper should record at least:

- Its identifier, full title, and current version;
- Version date and publication status;
- The relationship among Chinese, English, and other language versions;
- A suggested citation and the applicable license;
- The principal basis for the current revision;
- Known limitations, major objections, and unresolved questions.

Recommended public status labels are:

- **In preparation:** no full text has been released, and the paper is not presented as a completed project output;
- **Public discussion draft:** the paper may be cited, criticized, and revised, but has not been validated through project practice or formal peer review;
- **Revised discussion draft:** documented changes have been made in response to new literature, public criticism, or cross-paper consistency review;
- **Historical version:** no longer the current version, but retained so that the evolution of the theory remains visible.

---

## Sources, Privacy, and License

- Citations and references should be verifiable, with public sources, personal statements, reported material, and verified facts distinguished from one another;
- Copyrighted full texts should not be copied into this directory without permission;
- The identities, affiliations, contact details, and sensitive materials of workers, authors, reviewers, and contacts must not be disclosed without separate, explicit authorization;
- Worker participation, expert review, institutional collaboration, model results, and theoretical consensus must not be fabricated;
- After the rights holder confirms authorization for public release and the papers are merged, they will be covered by the repository's [CC BY-SA 4.0 content license](../../LICENSE-CONTENT); the license text governs the applicable rights and obligations. Before merge, an intended license must not be presented as an authorization already completed.

Return to the [foundational theory overview](../README.en.md).
