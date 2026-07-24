# 条目写作规范

七类条目的 content 子字段（schema 强制，写别的字段会被 contract 拒收）：

| type | 必填 | 可选 |
| --- | --- | --- |
| concept | definition | physical_notes, see_also（列表） |
| mechanism | claim | supporting_evidence, counter_evidence, controversy, testable_predictions（列表） |
| data_source | collection_method | known_biases, calibration_history, coverage |
| experiment_paradigm | design | metrics, pitfalls |
| hypothesis_template | structure | example, applicable_when |
| finding | statement, run_id | effect_size, uncertainty |
| counterexample | statement, run_id | effect_size, uncertainty |

写作线：

- title 一句话说清主张或对象，不写"关于……的研究"这类空泛表述。
- 每条必须有 source_type（literature/textbook/dataset_doc/historical_run/expert/derived）+
  source_ref（DOI/URL/run_id/审核人/书页）+ confidence（high/medium/low）+ valid_range。
- confidence 与证据强度对齐：单源摘要蒸馏默认 low、硬上限 medium；单次运行产出
  默认 low。high 需要可核验的独立多源、全文证据或跨运行复现，不能由调用者自由指定。
- valid_range 写适用边界（如 "SC21–SC25"、"仅 F10.7 调整后序列"），不知道就留空，不要编。
- related_ids 只填库中真实存在的条目 id。
- 条目正文写给读者看：不出现 schema 名、工具名、校验语言。
- 空可选字段直接省略，不要写 "N/A"、"暂无"。
