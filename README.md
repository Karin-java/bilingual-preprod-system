# 双语解说剧前筹系统

这是基于原始可用原型收敛出的最小版本：一个Skill、两份业务规则、两个确定性脚本。它不使用LangChain、RAG、数据库服务或脚本化模型批跑。

## 系统解决什么

1. 将DOCX、TXT或Markdown英文原稿无损拆为 `PNN` 章节。
2. 让当前Agent按章一次完成中英翻译、分场、声轨和章末索引。
3. 拒绝漏译、摘要化、零对白和错误索引等明显失败结果。
4. 将已通过章节的索引压缩成外部项目记忆，供后续章节继承译名和场景术语。
5. 全文完成后生成角色库、场景库、出镜统计、主要角色统计、资产候选清单和待确认问题。

用户始终按完整章节阅读和审核；场景只用于章节内导航和定向修改。

## 最小架构

```text
bilingual-preprod-system/
├─ SKILL.md
├─ references/
│  ├─ chapter-production.md
│  └─ full-story-consolidation.md
├─ scripts/
│  ├─ extract_chapters.py
│  └─ verify_and_collect.py
├─ agents/openai.yaml
└─ tests/test_minimal_pipeline.py
```

运行时只有两个脚本：

- `extract_chapters.py`：提取、PNN拆章、来源校验和项目初始化；不调用模型。
- `verify_and_collect.py`：完整性校验、进度检查点、项目记忆和全文复盘输入；不调用模型。

## 环境

- Python 3.10或更高版本。
- 处理TXT/Markdown不需要第三方依赖。
- 处理DOCX需要：

```text
python -m pip install python-docx
```

## 启动项目

```text
python scripts/extract_chapters.py "原稿.docx" --out "项目目录" --title "剧名"
```

初始化后先查看：

```text
项目目录/work/制作进度.md
```

Agent按照 [SKILL.md](SKILL.md) 读取下一章和 [单章生产规则](references/chapter-production.md)，输出：

```text
项目目录/deliverables/chapters/PNN.md
```

## 校验一个章节

```text
python scripts/verify_and_collect.py verify "项目目录" P03
```

校验通过后自动更新：

- `work/制作进度.md`
- `work/项目记忆.md`
- `work/全文复盘输入.md`
- `work/state.json`

中断后重新读取制作进度，从第一个未通过章节继续。

人工修改过章节后重新运行同一个校验命令。若批量修改了多个现有章节，可运行：

```text
python scripts/verify_and_collect.py collect "项目目录"
```

## 全文复盘

所有章节通过后，Agent读取 [全文复盘规则](references/full-story-consolidation.md) 和 `work/全文复盘输入.md`，生成：

- `角色信息库.md`
- `场景信息库.md`
- `全角色章节出镜表.md`
- `主要角色场景统计.md`
- `全文美术资产候选清单.md`
- `待确认问题.md`

默认只生成Markdown。DOCX属于用户点名后的单文件转换，不是本Skill的重复默认产物。

## 验证范围

当前版本已验证：

- CRLF文本按原字符重建。
- 没有Chapter标题时安全归入P01。
- 真实前180分钟DOCX提取为P00—P32，共176,382个规范化字符，重新拼接一致。
- 真实P03产生40个双语块、识别13段引号对白并通过来源覆盖检查。
- “英文整章保留、中文只写摘要、没有对白声轨”的伪合格结果会被拒绝。

自动校验不能替代人的翻译审美、分场判断和美术决定。它的职责是阻止明显不完整的结果流入下游。

## 维护入口

- 调整单章业务格式：`references/chapter-production.md`
- 调整最终交付和身份归并：`references/full-story-consolidation.md`
- 调整流程顺序和硬边界：`SKILL.md`
- 调整拆章：`scripts/extract_chapters.py`
- 调整完整性校验：`scripts/verify_and_collect.py`

不要把语义规则写进脚本，也不要把确定性拆章和校验交给模型。
