# 前筹系统数据与扩展契约 v2

## 1. 设计原则

- 将 `source/` 作为只读事实源；任何翻译、分析或渲染均不得覆盖原文。
- 将 JSON/JSONL 作为事实层，将 Markdown/CSV/报告/提示词作为可重建的派生产物。
- 为章节、原文单元、场景、节拍和角色使用稳定 ID；已发布 ID 不得因重跑而改变。
- 为模型产生的结论保存证据、判断状态和置信度；未知信息使用 `unknown`，不得猜测。
- 使用 `schema_version` 管理兼容性。补充可选字段为次版本升级；删除、改名或改变语义为主版本升级。
- 让下游模块只消费结构化数据，不重新解析 Markdown，也不重新读取整本原文。

## 2. 项目目录

```text
project/
├── project.json
├── source/
│   ├── original.txt
│   ├── original.input.ext
│   ├── manifest.json
│   └── chapters/p01.txt
├── data/
│   ├── units/p01.units.jsonl
│   ├── analysis/p01.analysis.json
│   ├── reviews/p01.events.jsonl
│   ├── views/analysis/p01.resolved.json
│   ├── views/analysis/p01.resolved.manifest.json
│   ├── render/p01.render-manifest.json
│   ├── registries/characters.json
│   ├── registries/locations.json
│   ├── registries/terminology.json
│   ├── appearances.jsonl
│   └── profiles.json
├── work/
│   ├── analysis/p01.packet.json
│   └── review/p01.review.md
├── deliverables/
│   ├── scripts_bilingual/p01.md
│   ├── 全角色章节出镜表.csv
│   ├── 主要角色场景统计.md
│   └── 角色基础信息档案.md
└── extensions/
    ├── reports/
    └── art-prompts/
```

## 3. 核心记录

机器格式分别由 `schemas/` 下的 JSON Schema 定义：

- `source-unit.schema.json`：一个不可变原文单元；不承载翻译或语义分析。
- `text-segment.schema.json`：原文单元内按字符区间划分的叙述、动作、对白等语义片段及说话人判断。
- `analysis-packet.schema.json`：单章、最小上下文的语义分析工作包。
- `chapter-analysis.schema.json`：一章的文本片段、生产场景和场内 Beat；只引用原文单元 ID。
- `review-event.schema.json`：针对章、Scene、Beat、原文单元或片段的只追加人工验收事件。
- `resolved-view-manifest.schema.json`：基础分析、事件日志和当前解析视图之间的哈希及依赖关系。
- `render-manifest.schema.json`：当前解析视图与双语 Markdown、验收清单之间的哈希及依赖关系。
- `project.schema.json`：项目状态及已登记产物。
- `source-manifest.schema.json`：原始文件、正文基线、拆章范围和指纹。
- `character.schema.json`：全局角色、别名、角色类型及首次出镜。
- `appearance.schema.json`：角色的一次出镜或被提及记录。
- `character-profile.schema.json`：人物参数、证据、明确/推断/冲突/未知状态。
- `extension-manifest.schema.json`：报告、美术提示词等扩展模块的输入输出声明。

JSONL 文件每行必须是一个完整 JSON 对象，并独立符合相应 Schema。不得把跨行 JSON 写入 JSONL。

## 4. 稳定 ID

| 对象 | 格式 | 示例 |
|---|---|---|
| 项目 | `PRJ-{slug}` | `PRJ-violette` |
| 章节 | `P{NN}` | `P01` |
| 原文单元 | `{chapter}-U{NNNN}` | `P01-U0001` |
| 场景 | `{chapter}-S{NNN}` | `P01-S001` |
| Beat | `{scene}-B{NNN}` | `P01-S001-B001` |
| 角色 | `CHAR-{NNNN}` | `CHAR-0001` |
| 地点 | `LOC-{NNNN}` | `LOC-0001` |
| 出镜记录 | `APP-{chapter}-{NNNN}` | `APP-P01-0001` |

编号依据源顺序或首次确认顺序生成。修正名称、译文或人物属性时不得更换 ID。
`P00` 专用于第一个章节标题之前确实存在的前置文本；正文首章从 `P01` 开始。超过 99 章后自然扩展为 `P100`。

### 原文基线

- TXT/Markdown：保存原文件逐字节副本；解码后不转换换行、空白或标点。UTF BOM 只作为编码标记移除，并在清单中记录编码。
- DOCX：保存原始 DOCX 逐字节副本；按文档 XML 顺序提取正文文本，以段落换行形成明确的 UTF-8 正文基线。完整性指纹同时覆盖原 DOCX 和正文基线；页眉、页脚、脚注、尾注或批注等辅助部件进入警告清单，不得静默忽略。
- 各章节文本必须是正文基线的连续、不重叠切片；按清单顺序拼接后必须与正文基线完全相同。
- 原文单元采用物理行作为稳定最小单元并保留行尾字符；按单元顺序拼接后必须与对应章节完全相同。

## 5. 证据与不确定性

- `explicit`：原文明确给出。
- `inferred`：可由上下文合理推断，必须给出证据和说明。
- `user_confirmed`：由用户在人工验收中明确补充或确认；原始 Agent 判断仍保留在基础分析中。
- `conflict`：不同证据互相矛盾，不得自动覆盖。
- `unknown`：当前材料无法确定，值使用 `null`。

所有语义结论至少引用一个 `source_unit_id`；只有 `unknown` 可以没有证据。置信度范围为 0 到 1，仅表示判断把握，不代替证据。

### 待确认与解析视图

- 基础分析存在未知或冲突时使用 `review.status = provisional`，但只要结构和证据完整即可通过阶段校验。
- 人工修订只追加到 `data/reviews/`，不得覆盖基础分析；解析视图由基础分析与有效事件确定性重建。
- 下游模块必须优先读取 `data/views/analysis/pNN.resolved.json`；若不存在才读取 `data/analysis/pNN.analysis.json`。
- 解析视图的 manifest 保存基础分析、事件日志和视图哈希。任一输入变化时，只将对应章节及其下游产物标为需重建。
- 详细操作与安全边界见 `references/review_workflow.md`。

### 用户交付格式

- 默认只生成清洗后的 Markdown，不同时复制一份 DOCX。
- `deliverables/scripts_bilingual/pNN.md` 是单章中英标准剧本；`work/review/pNN.review.md` 是过程验收文件，不属于重复交付件。
- 用户明确选择某一个 Markdown 文件时，才对该文件执行确定性 DOCX 转换，并保存源文件哈希；转换不得再次调用模型。

## 6. 场景与 Beat

- 场景表示可用于制片的时空单元，必须记录内/外景、具体地点和时间状态。
- 地点、内外景、时间线或现实层级变化时新建场景。
- 同一时空中目标、冲突、信息或情绪发生重大变化时新建 Beat，不虚构新地点。
- 无法可靠确定场景字段时使用 `unknown` 并进入人工复核，不得填写“日常卧室”等泛化名称。

## 7. 扩展接口

扩展模块在自己的目录包含 `extension.json`，并符合 `extension-manifest.schema.json`。

扩展必须：

1. 声明 `consumes` 和 `produces` 的数据类型及版本范围。
2. 仅读取核心事实层；不得修改 `source/` 或覆盖 `data/` 中的核心记录。
3. 将产物写入 `extensions/{module_id}/` 或 `deliverables/` 中声明的目标。
4. 保存输入内容哈希、模块版本、生成时间和产物路径，使结果可追溯、可重建。
5. 使用自己的命名空间保存扩展字段，禁止向核心 Schema 随意塞入模块私有字段。

预留模块：

- `reports`：消费章节分析、出镜记录和人物档案，生成前筹汇总报告。
- `art-prompts`：消费场景、地点、角色形象、服装及证据，生成美术资产提示词。

核心层只保证稳定事实和引用关系，不规定报告版式或提示词模板。

## 8. 阶段验收

- 所有 Schema 文件是合法 JSON。
- 示例 ID、枚举和字段约束可由标准 JSON Schema Draft 2020-12 校验器读取。
- 核心记录均能通过稳定 ID 相互引用。
- 扩展模块无需修改核心 Schema 即可新增。
- Markdown、CSV、报告和提示词删除后，可从事实层重新生成。
