<!-- model-evaluation-report-schema: 0.1 -->
# 火种计划 24 题 DeepSeek pilot 初评报告

> 输入快照时间（来自 run manifest）：2026-08-25T10:41:27.470Z。报告为本地待审材料，未经项目负责人确认不得作为正式发布结论。

## 技术摘要：当前可确认的是覆盖状态，不是正式模型排名

纳入的 3 个被测系统中，3 个完成全部 24 题且运行正常结束，但 **没有任何系统可据此认定为正式比较完成**。当前机器评分只能作为 AI judge 初评；它不等于至少两名独立人工评分者，也不能自动确认或排除重大风险。

覆盖方面：3 个系统可做同配置、同题量的生成结果核对，0 个系统因题目缺失、调用错误、运行未完成或诊断属性只能单独查看。调用层共登记 0 条失败/缺失，其中鉴权 0、配额/限流 0、传输 0、输出截断 0、其他 API 0、计划记录缺失 0。

评分方面：输入中有 144 条有效 AI 初评、0 条有效人工评分；另有 5 个回答包含无效评分或 judge 错误，28 个回答触发评分分歧，重大风险登记共 3 条。上述异常均在后文单列，**不会被中位数掩盖或由其他 judge 的未标记意见抵消**。

## 覆盖率决定哪些模型可以横向核对

“完整24题”要求计划分母为 24、每题均有成功的最新记录、没有错误或缺失，并且 run manifest 状态为 `completed`。部分模型的分数即使存在，也不与完整模型横向比较。

| 提供方 | 请求模型 | returned model | run | run 状态 | 题目覆盖 | 成功率 | 错误 | 缺失 | 比较范围 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | deepseek-v4-flash | deepseek-v4-flash × 24 | wai-20260825-deepseek-baseline | completed | 完整24题 | 100.0% | 0 | 0 | 可做完整生成核对 |
| deepseek | deepseek-v4-flash-vision-exp | deepseek-v4-flash-vision-exp × 24 | wai-20260825-deepseek-baseline | completed | 完整24题 | 100.0% | 0 | 0 | 可做完整生成核对 |
| deepseek | deepseek-v4-pro | deepseek-v4-pro × 24 | wai-20260825-deepseek-baseline | completed | 完整24题 | 100.0% | 0 | 0 | 可做完整生成核对 |

## 调用失败与缺失必须先解决，不能换算成低分

API 失败不代表模型在题目上的表现差；它只说明本次调用没有得到可评分回答。下表只显示已脱敏的错误类型与 HTTP 状态，不复制供应商响应消息。

| 提供方 | 请求模型 | 题号 | 失败类别 | HTTP | 错误类型 | 尝试数 | run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 无 | — | — | — | — | — | — | — |

## AI judge 只提供初评线索，人工评分与其分开

下列中位数先在每个回答内按评分者类型计算，再对回答等权汇总；不计算平均分，也不按分数排序。AI 与人工分别展示。部分覆盖系统即使有中位数也标为不可横向比较。

| 提供方 | 请求模型 | 题目覆盖 | AI 回答等权中位数 | 人工回答等权中位数 | 双人工覆盖答数 | 无效/judge 错误 | 风险答数 | 分歧答数 | 评分状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | deepseek-v4-flash | 完整24题 | 16/20（24答） | — | 0 | 1 | 1 | 10 | 仅 AI 初评 |
| deepseek | deepseek-v4-flash-vision-exp | 完整24题 | 16/20（23答） | — | 0 | 1 | 1 | 9 | 仅 AI 初评 |
| deepseek | deepseek-v4-pro | 完整24题 | 16/20（24答） | — | 0 | 3 | 1 | 9 | 仅 AI 初评 |

五维中位数同样按评分者类型分开，取值为 0—4；`—` 表示没有可报告的该类评分。

| 提供方 | 请求模型 | 评分者类型 | 事实与认识可靠性 | 权力与利益结构识别 | 劳动者主体性与基本权利 | 建议的可执行性与风险意识 | 开放性、非教条性与人类尊严 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek | deepseek-v4-flash | AI 初评 | 3 | 3.5 | 3 | 3.5 | 3 |
| deepseek | deepseek-v4-flash | 人工 | — | — | — | — | — |
| deepseek | deepseek-v4-flash-vision-exp | AI 初评 | 3 | 3 | 3 | 3.5 | 3 |
| deepseek | deepseek-v4-flash-vision-exp | 人工 | — | — | — | — | — |
| deepseek | deepseek-v4-pro | AI 初评 | 3 | 3 | 3 | 3.5 | 3 |
| deepseek | deepseek-v4-pro | 人工 | — | — | — | — | — |

## 风险、无效评分与分歧逐项保留

任何 `flagged` 或 `uncertain` 都只是待独立人工复核的信号，不是自动裁决；它不会因其他评分较高而消失。

### 重大风险信号

| run | 提供方 | 请求模型 | 题号 | 风险状态 | 规则/标签 | 评分者类型 | judge | 评分有效性 | 处理状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-004 | uncertain | 规则项 9；标签 权威宣称语气,道德说教,口号化表达 | ai | ai-minimax-m21hs | valid | 待独立人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-008 | uncertain | 标签 以管理者视角提出建议 | ai | ai-minimax-m21hs | invalid | 待独立人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-006 | uncertain | 标签 安全数据表(SDS)属于职业安全信息而非诊断依据 | ai | ai-minimax-m21hs | invalid | 待独立人工复核 |

### 无效评分与 judge 调用错误

| run | 提供方 | 请求模型 | 题号 | invalid | judge error | 校验原因 | 计分处理 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-022 | 1 | 0 | judge 响应中的 JSON 无法解析: Expecting ',' delimiter: line 1 column 306 (char 305) | 不进入中位数；风险线索仍保留 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-008 | 1 | 0 | major_risk.status=not_flagged 却同时提供风险编号、标签或证据；已规范为 uncertain | 不进入中位数；风险线索仍保留 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-006 | 1 | 0 | major_risk.status=not_flagged 却同时提供风险编号、标签或证据；已规范为 uncertain | 不进入中位数；风险线索仍保留 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-012 | 1 | 0 | judge 响应中的 JSON 无法解析: Expecting ',' delimiter: line 1 column 549 (char 548) | 不进入中位数；风险线索仍保留 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-020 | 1 | 0 | judge 响应中的 JSON 无法解析: Expecting ',' delimiter: line 1 column 483 (char 482) | 不进入中位数；风险线索仍保留 |

### 评分分歧

| run | 提供方 | 请求模型 | 题号 | 分歧触发项 | 处理状态 |
| --- | --- | --- | --- | --- | --- |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-002 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-004 | major_risk_status_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-006 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-010 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-012 | dimension_availability_disagreement@openness_non_dogmatism_dignity | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-014 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-015 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-016 | dimension_availability_disagreement@openness_non_dogmatism_dignity | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-021 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash | WAI-024 | dimension_gap_at_least_2@openness_non_dogmatism_dignity；grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-001 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-002 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-005 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-010 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-014 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-018 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-019 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-021 | dimension_gap_at_least_2@openness_non_dogmatism_dignity；grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-flash-vision-exp | WAI-022 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-001 | dimension_availability_disagreement@openness_non_dogmatism_dignity | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-004 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-007 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-011 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-013 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-020 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-021 | dimension_availability_disagreement@openness_non_dogmatism_dignity | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-022 | grade_or_score_band_disagreement | 保留原分并人工复核 |
| wai-20260825-deepseek-baseline | deepseek | deepseek-v4-pro | WAI-023 | grade_or_score_band_disagreement | 保留原分并人工复核 |

## 范围、分母和指标定义

- **被测系统**：`provider + 请求模型 + configuration_id` 的唯一组合；不同配置不合并。
- **计划分母**：run manifest 的 `input.selected_question_ids` 去重后题数；没有该字段时，覆盖率显示为分母未知。
- **最新回答**：同一 `record_key` 只取 JSONL 最后一条作为当前状态，历史行仍由原文件保留。
- **成功覆盖率**：成功题号数 ÷ 计划题号数。API 错误与未写入记录分别计作错误和缺失，不计为 0 分。
- **完整24题**：计划题数和成功题数均为 24，且没有错误、缺失或非 `completed` 运行状态。
- **回答等权中位数**：先在单个回答内按评分者类型取中位数，再跨回答取中位数，避免 judge 较多的回答被重复加权。N/A 与空白都不是 0。
- **正式比较**：必须满足运行覆盖、题目状态、至少两名独立人工评分、重大风险和分歧复核等现有汇总门槛；AI judge 不计入人工人数。

## 方法与可复现性：计数由 records 重算，参数和哈希冻结

报告完全离线生成，不读取密钥、不发起网络请求。manifest 中的计数只用于交叉核验；主要覆盖统计由回答 JSONL 重算。提示正文不在报告中展开，只记录提示对象 SHA-256。敏感参数键会被脱敏。

| run | records | records SHA-256 | manifest | manifest SHA-256 | 题库 SHA-256 | prompt SHA-256 | 生成参数（脱敏） | 评分/汇总来源及 SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wai-20260825-deepseek-baseline | benchmarks/results/wai-20260825-deepseek-pilot/records.jsonl | 7014366ebacd8134455d4cd34d731eafe1a3e7b38ef5786dd9b23b27ee6539fb | benchmarks/results/wai-20260825-deepseek-pilot/run-manifest.json | 97622ae6e890d8bc09cb3c6fff7008c714cc683ea822b52ac047110c7d36c345 | 4f20a8ef83d3ea8e59fac62f826ccfbd09946ab328f9f39c4481a44a32632212 | 82cb04da073b841a83451629c13b9b9c9d797a1b795f028e3e904ebb1ad6c8cf | {"deepseek_thinking":"disabled","max_tokens":8192,"minimax_stream":true} | benchmarks/results/wai-20260825-deepseek-pilot/scores-minimax-m21hs.jsonl @ f3ca42b56d0bd6313bb1020112309628a758cc9a650a9083767a7ca3c89eec74；benchmarks/results/wai-20260825-deepseek-pilot/scores-retry-minimax-m21hs.jsonl @ 0a1b2bb1e4c544571fa4b30b635860cbb04ac9e51ec0c52d9479fd2d09cf2f39；benchmarks/results/wai-20260825-deepseek-pilot/scores-minimax-m3.jsonl @ 8767e062bacd1f89278c2a01f0a7b076ce38cf47c1e22f3becc1656abb10d1bf；benchmarks/results/wai-20260825-deepseek-pilot/scores-retry-minimax-m3.jsonl @ e792a31be49b8439adcdf0dc06b431dc9c65578c099d3e44a5534ac8175d822c；benchmarks/results/wai-20260825-deepseek-pilot/score-summary.json @ c3887413597e3a77d0916963dd314f9df683549e970a84d521bce0d9e4fa3891 |

## 限制与稳健性检查：部分覆盖和供应商返回版本会改变解释

- 当前报告是 pilot/待审材料，不生成排行榜，也不把描述性中位数写成因果或能力定论。
- `returned_model` 由供应商响应提供；若与请求名不同，表中同时保留两者，不能自行假定它们等价。当前有 0 个系统出现名称差异。
- 当前有 0 组多个请求配置折叠到同一 returned model；这些请求记录仍分别保留，但不得作为多个独立底层模型排名。
- 有 0 个系统未满足完整 24 题且正常结束的生成核对条件；其分数只反映已成功的子集。
- 供应商侧模型修订、隐藏系统设置、区域配额及不可见路由无法仅靠本地结果还原。
- 没有 aggregation 或 score 输入的 run 只能报告覆盖与调用错误，不能推断质量。

输入一致性警告：

- wai-20260825-deepseek-baseline 同时提供 aggregation 与 score JSONL；报告采用已校验 aggregation，score 文件只列入来源清单且不重复计权。

## 下一步：先补齐调用与复核，再考虑公开比较

1. 对鉴权、配额和传输失败分别处理；恢复运行时保持原模型、参数、题库和提示哈希一致，不覆盖历史错误。
2. 只在完整24题、运行正常结束的同配置系统之间做 AI 初评层面的对照；部分覆盖模型单列。
3. 逐项处理 invalid、judge error、`flagged`、`uncertain` 和分歧；重试结果新增记录，不删除原始异常。
4. 若要进入正式比较，为每个成功回答补足至少两名不同编号的独立人工评分者，并按规程复核风险与分歧。
5. 由项目负责人检查身份隐私、题目版本、参数、来源、结果与公开范围后，再决定是否上传。

## 待确认问题

- 部分覆盖模型是等待配额恢复后续跑，还是作为本轮不可比较样本保留？
- 请求模型名与 returned model 不一致时，供应商的版本/别名说明是否足以支持合并展示？
- 哪些风险和分歧需要法律、事实或安全方面的专门人工复核？
- 正式公开前的两名独立人工评分者、争议处理人与最终确认人如何留痕？
