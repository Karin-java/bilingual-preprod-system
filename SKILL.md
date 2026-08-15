---
name: bilingual-preprod-system
description: 面向解说剧生产的轻量双语前筹系统。无损拆分英文原稿，逐章生成中英标准剧本与 Scene/Beat，建立全剧角色、场景、出镜和美术资产候选数据库，并支持按问题编号进行局部人工验收。用于完整剧本前筹、英文小说改编整理、角色/地点统一、生产资产范围盘点及单文件 DOCX 导出。
---

# 双语解说剧前筹系统 V3

## 工作目标

用“单章一次语义处理 + 本地确定性合并”完成：

1. 英文原稿无损拆章，编号为 `PNN`。
2. 中英标准剧本，区分题头、叙述、动作、对白、内心和分隔内容。
3. 按生产时空切 Scene，按目标、冲突、信息或重大情绪变化切 Beat。
4. 全剧角色、地点、出镜与人物基础信息数据库。
5. 全文美术资产候选清单，由用户决定制作范围。
6. 按章节、Scene、Beat 或问题编号进行局部人工修改和撤销。

系统只整理剧情事实，不替用户决定审美。默认交付 Markdown；用户点名某一个文件时才转换 DOCX。

## 必守边界

- 英文原文一字不改；每章 `blocks[].text_en` 拼接必须重建章节源文件。
- 一次只处理 `work/pipeline/next-task.json` 指定的一章。
- 不自行编写批量模型 API 脚本，不并发重复请求，不要求普通用户配置外部模型 API。
- 章节通过校验后立即形成检查点；中断后运行 `resume` 继续。
- 证据只引用块编号，不重复粘贴原文。
- 不猜测未知事实；制片相关未知项进入用户验收问题。
- 不自动生成造型、色彩、灯光、镜头、构图、建筑风格或美术提示词。
- 不重复输出同一内容的多种格式。

详细数据和交付模板见 `references/data_contract.md`；语义口径见 `references/analysis_rules.md`。

## 启动完整项目

```text
python scripts/run_pipeline.py start <source-file> --project-dir <project> --title <title>
```

支持 TXT、Markdown 和 DOCX。启动后读取：

- `deliverables/制作进度.md`
- `work/pipeline/next-task.json`

## 执行单章任务

1. 读取 `next-task.json`。
2. 只读取其中的 `input_path` 和 `schema_path`。
3. 直接用当前 Agent 的语义能力生成声明的 `output_path`；不要调用外部模型脚本。
4. 分析任务把 `chapter_input_sha256` 写入章节结果；修复任务保留该值，以始终绑定原始单章包。
5. 写入 JSON 后运行声明的校验命令。
6. 通过后运行：

```text
python scripts/run_pipeline.py resume <project>
```

若用户要求全本处理，持续重复以上步骤。每完成一章都先形成磁盘检查点；上下文将满或任务被中断时，停止在检查点，不把多章合并成一个长请求。

校验失败时，管线会生成 `repair_chapter`。只修复列出的错误和直接依赖，不重做已通过章节。

## 单章分析口径

- 场景名必须具体，例如“王宫·维克多的书房”“王宫·北侧走廊”。
- 地点、内外景、时间线或现实层级变化时切 Scene。
- 同一时空内目标、冲突、信息或重大情绪变化时切 Beat。
- 对白填写说话人；不明说话人保留 `unknown` 并建立问题。
- 登记具名角色、有台词角色、稳定身份角色和高频群众角色。
- 只被谈论不算实际出镜。
- 人物事实仅提取年龄、性别、种族、身份、外貌、发型、体型、特殊标记、服装和性格。
- 地点事实仅提取空间、环境、陈设和状态变化。
- 普通角色与场景资产由全局程序自动列出；单章只补充明确服装、道具或视觉状态变体。

## 全局数据库与身份归并

章节通过后，本地程序增量更新：

- `data/catalogs/catalog.json`
- `deliverables/角色信息库.md`
- `deliverables/场景信息库.md`
- `deliverables/全角色章节出镜表.md`
- `deliverables/主要角色场景统计.md`
- `deliverables/全文美术资产候选清单.md`
- `deliverables/待确认问题汇总.md`

后文确认两个记录是同一角色或地点时，使用通用归并事件。旧章节不重写；旧编号和全部名称索引到统一实体：

```text
python scripts/manage_entities.py merge <project> --type character --source CHAR-0008 --target CHAR-0002 --note "确认 King 即 Viktor"
```

不要以显示名称作为下游主键。归并与资料决定可撤销，规则见 `references/review_workflow.md`。

## 人工验收

普通用户只阅读 Markdown，不编辑 JSONL。问题之间必须有清晰分隔，显示问题编号、位置、英文线索和中文参考，不显示内部字段路径或当前值。

用户可以针对一章、一个 Scene、一个 Beat 或一个文本块补充和修改。Agent 将自然语言答复转换成事件：

```text
python scripts/review_analysis.py set <project> --chapter P03 --target P03-S001 --field time --value-json '"NIGHT"' --note "用户确认"
python scripts/review_analysis.py resolve <project> --chapter P03 --issue ISS-P03-0001 --resolution "接受未知" --note "全书复盘完成"
```

资产范围由用户逐项决定：

```text
python scripts/manage_assets.py decide <project> --asset ASSET-0001 --status approved --note "确认制作"
python scripts/manage_assets.py merge <project> --asset ASSET-0004 --target ASSET-0002 --note "共用资产"
python scripts/manage_assets.py split <project> --asset ASSET-0002 --name "受损状态" --scene P12-S003 --note "单独设计"
python scripts/manage_assets.py decide <project> --asset ASSET-0007 --status excluded --note "无需制作"
```

完成验收后再次运行 `resume`。所有问题已处理、所有资产已确认或排除时，项目状态为 `complete`。

## 可选 DOCX

只有用户明确选择某一个 Markdown 时运行：

```text
python scripts/convert_to_docx.py <selected.md> --output <selected.docx>
```

转换不调用模型，不批量复制全部交付文件。

## 维护与验证

```text
python scripts/validate_schemas.py
python -m unittest discover -s tests -v
python scripts/validate_pipeline.py <project>
```

维护入口：

- 数据边界与模板：`references/data_contract.md`
- 单章语义口径：`references/analysis_rules.md`
- 人工验收：`references/review_workflow.md`
- 断点、性能和失败处理：`references/pipeline_rules.md`
- 单章 Schema：`schemas/chapter.schema.json`
- 调度器：`scripts/run_pipeline.py`
- 全局数据库：`scripts/build_catalogs.py`
- 后续扩展：`extensions/README.md`
