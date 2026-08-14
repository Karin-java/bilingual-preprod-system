---
name: bilingual-preprod-system
description: 解说剧全流程前筹系统：无损拆分英文原始剧本，生成按场景与情节节拍组织的中英标准剧本，区分动作、叙述和对白，建立角色、地点、出镜、主要角色场景及人物基础信息的证据化数据，并为报告整理和美术资产提示词提供可扩展接口。用于英文剧本或小说的双语前筹、分场、角色统计、人物分析及相关派生产物生成。
---

# 解说剧前筹系统

## 核心原则

1. 将英文原文作为不可变事实源；保留字符、标点、大小写、段落和顺序。
2. 只做一次语义抽取；让后续表格、报告和提示词复用结构化事实，不重复读取整本原文。
3. 区分生产场景与场内 Beat：时空变化时切场，目标、冲突、信息或重大情绪变化时切 Beat。
4. 使用可用于制片的具体场景名，记录归属主体、具体空间、内外景和时间。
5. 为角色、场景、出镜和人物信息保存原文证据；无法确定时标记 `unknown`，不得猜测。
6. 将 JSON/JSONL 作为事实层，将 Markdown、CSV、报告和提示词作为可重建的派生产物。

## 工作流

按以下阶段顺序执行，并允许从最近一个通过校验的阶段断点续跑：

1. **无损接收**：保存原始文件、拆分章节、生成稳定段落 ID 和 SHA-256 指纹。
2. **逐章结构化**：识别叙述、动作、对白、心理和特殊文本，生成中英一一对应记录。
3. **场景分析**：建立生产场景和场内 Beat，所有边界引用原文单元 ID。
4. **实体沉淀**：增量维护统一角色、多名称索引、旧 ID 重定向、地点和术语；不要为每章重新建立全局信息。
5. **出镜统计**：记录具名角色、有台词角色、稳定身份角色和群众角色的出镜或被提及状态。
6. **人物档案**：提取年龄、性别、种族、身份、外貌、发型、体型、特殊标记、服装和性格，并区分明确、推断、冲突和未知。
7. **确定性渲染**：从事实层生成中英标准剧本、全角色章节出镜表、主要角色场景统计和人物档案。
8. **质量校验**：检查原文完整性、引用完整性、ID 稳定性、跨章一致性和派生产物可重建性。

## 场景与角色判定

- 场景标题至少包含内/外景、具体地点和时间；优先使用“归属主体 + 具体空间”。
- 地点、时间线、内外景或现实层级变化时开始新场景。
- 同一时空内的目标变化、冲突升级、信息揭露、权力逆转或重大情绪变化记录为新 Beat。
- 角色实际行动或在场说话时记录出镜；仅被谈论时记录为被提及。
- 为管家、园丁等具有连续性的身份角色分配稳定角色 ID；不要与无连续性的群众群体混为一体。
- 无法确认对白说话人时保留台词并标记未知说话人。
- 主要角色由系统根据剧情作用提出候选，再由用户确认业务定位。

## 数据和扩展

执行前读取 `references/data_contract.md`，并让事实记录符合 `schemas/*.schema.json`。

新增报告、美术资产提示词或其他能力时：

1. 通过扩展清单声明所消费的数据类型和版本。
2. 仅读取核心事实层，不覆盖原文或核心记录。
3. 将模块私有数据写入自己的命名空间。
4. 保存输入哈希、模块版本和输出路径，使产物可追溯、可重建。

## 当前执行入口

优先使用总管线启动或续跑。它每次只暴露一个有界任务，并自动复用已经通过校验的阶段：

总管线运行时读取 `references/pipeline_rules.md`；不要绕过唯一下一任务一次加载多章。

```text
python scripts/run_pipeline.py start <source> --project-dir <project>
python scripts/run_pipeline.py resume <project>
python scripts/run_pipeline.py status <project>
python scripts/validate_pipeline.py <project>
```

执行 `work/pipeline/next-task.json` 声明的唯一任务。若为全书人物复盘且完整材料仍无法确认剩余参数，验收当前角色包后继续：

```text
python scripts/run_pipeline.py ack-recap <project> --character CHAR-0001 --note <review-result>
```

需要单独诊断阶段时再使用以下细分入口：

```text
python scripts/ingest_source.py <source> --project-dir <project>
python scripts/validate_ingest.py <project>
python scripts/prepare_analysis.py <project> --chapter P01
```

读取 `references/analysis_rules.md` 和生成的 `work/analysis/p01.packet.json`，将结构化结果写入工作包声明的 `output_path`，然后执行：

```text
python scripts/validate_analysis.py <project> --chapter P01
python scripts/review_analysis.py materialize <project> --chapter P01
python scripts/render_chapter.py <project> --chapter P01
python scripts/validate_render.py <project> --chapter P01
python scripts/build_registries.py <project>
python scripts/validate_registries.py <project>
python scripts/build_profiles.py <project>
python scripts/validate_profiles.py <project>
python scripts/build_appearance_reports.py <project>
python scripts/validate_appearance_reports.py <project>
```

后文确认两个角色记录属于同一人时，通过通用身份事件归并；不要重写早期章节：

```text
python scripts/manage_character_identities.py merge <project> --source CHAR-0008 --target CHAR-0002 --canonical-name Viktor --chinese-name 维克托 --note <evidence-or-user-confirmation>
python scripts/manage_character_identities.py retract <project> --event IDENT-000001 --note <reason>
```

后文章节或全书复盘确认人物参数时，追加全局资料决定；旧章节事实和证据保持不变：

```text
python scripts/manage_profile_decisions.py set <project> --character Viktor --field story_role --value 男主 --note <依据或用户确认>
python scripts/manage_profile_decisions.py retract <project> --event PROFILE-000001 --note <reason>
```

结构与证据通过即可继续；`provisional` 表示仍有待确认项，不是校验失败。人工验收时读取 `references/review_workflow.md`，追加精准修订并物化当前视图：

```text
python scripts/review_analysis.py set <project> --chapter P01 --scope P01-S001 --field time_of_day.value --value-json '"night"' --note <reason> --resolve ISS-P01-0001
python scripts/review_analysis.py note <project> --chapter P01 --scope P01 --note <supplement>
python scripts/review_analysis.py retract <project> --chapter P01 --event REV-P01-000001 --note <reason>
```

只处理当前章节；校验通过前不要进入下一章或下游渲染。下游优先读取 `data/views/analysis/pNN.resolved.json`。

## 资源

- `references/data_contract.md`：事实层、稳定 ID、证据链、目录和扩展契约。
- `schemas/*.schema.json`：核心记录和扩展清单的机器校验规则。
- `scripts/validate_schemas.py`：校验 Schema 语法和本地引用。
- `scripts/ingest_source.py`：无损接收 TXT、Markdown 或 DOCX，拆章并生成稳定原文单元。
- `scripts/validate_ingest.py`：校验原文件、正文基线、章节和原文单元的指纹及重组完整性。
- `references/analysis_rules.md`：文本片段、说话人、Scene、Beat、证据和人工复核规则。
- `scripts/prepare_analysis.py`：为单个章节生成最小上下文分析工作包及输入哈希。
- `scripts/validate_analysis.py`：校验字符覆盖、说话人、场景/Beat分区、证据和复核状态。
- `references/review_workflow.md`：待确认项、精准人工修订、撤销和下游读取规则。
- `scripts/review_analysis.py`：追加验收事件并确定性重建当前章节解析视图。
- `scripts/render_chapter.py`：从当前解析视图生成唯一的中英 Markdown 剧本和双语验收清单，不调用模型。
- `scripts/validate_render.py`：校验英文原文、中文译文、Scene、Beat、待确认线索和输出哈希。
- `references/entity_rules.md`：具名人物、有台词角色、身份角色、群众候选、统一身份解析、别名匹配和地点登记规则。
- `scripts/build_registries.py`：从所有当前解析视图和身份事件确定性重建统一角色、多名称索引和地点登记表。
- `scripts/manage_character_identities.py`：追加或撤销角色归并决定，不回写章节分析。
- `scripts/validate_registries.py`：校验实体 ID、名称索引、旧 ID 重定向、跨章链接、候选项和人工查看版可重建性。
- `references/appearance_rules.md`：全角色章节对应矩阵、每个主要角色独立出镜表、实际出镜、梦境/回忆、仅被提及和统一身份去重规则。
- `scripts/build_appearance_reports.py`：生成机器出镜事实、角色为行且 P章节为列的出镜矩阵，以及主要角色场景统计 Markdown。
- `scripts/validate_appearance_reports.py`：校验稳定出镜 ID、统一角色去重、场景摘要和两份 Markdown 的可重建性。
- `scripts/build_profiles.py`：按统一角色永久编号聚合逐章资料事实，生成角色档案和只含未决项的全书复盘清单。
- `scripts/manage_profile_decisions.py`：追加或撤销全局人物资料确认，不回写已经完成的章节。
- `scripts/validate_profiles.py`：校验必需人物参数、证据、跨章归并、未知项和 Markdown 可重建性。
- `references/pipeline_rules.md`：唯一下一任务、阶段失效边界、复用、单角色复盘和成本统计规则。
- `scripts/run_pipeline.py`：启动或续跑总管线，生成机器状态、唯一下一任务和用户可读制作进度。
- `scripts/validate_pipeline.py`：校验断点状态、任务输入指纹、复用产物、复盘事件和制作进度可重建性。

每完成一个流水线阶段即运行对应校验。失败时只返修受影响的章节、场景或记录，不重跑已通过的全量数据。
