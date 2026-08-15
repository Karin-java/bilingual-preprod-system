# V3 人工验收与局部重建

普通用户只阅读：

- `work/review/pNN.review.md`
- `deliverables/待确认问题汇总.md`
- `deliverables/全文美术资产候选清单.md`

用户按编号回复即可，不直接编辑 JSON 或 JSONL。Agent 将答复转换成只追加事件。

## 单章修改

```text
python scripts/review_analysis.py set <project> --chapter P03 --target P03-S001 --field time --value-json '"NIGHT"' --note "用户确认"
python scripts/review_analysis.py resolve <project> --chapter P03 --issue ISS-P03-0001 --resolution "原文未说明，用户接受未知" --note "全书复盘完成"
python scripts/review_analysis.py note <project> --chapter P03 --target P03-S001 --note "用户补充"
python scripts/review_analysis.py retract <project> --chapter P03 --event REV-P03-000001 --note "撤销"
```

允许修改中文翻译、分类、说话人、Scene、Beat、事实增量和问题状态。禁止修改 `text_en`、源哈希、章节编号和输入哈希。事件失效时只重建当前章、全局小型目录和对应 Markdown，不重新分析其他章节。

## 角色或地点归并

```text
python scripts/manage_entities.py merge <project> --type character --source CHAR-0008 --target CHAR-0002 --note "确认 King 即 Viktor"
python scripts/manage_entities.py separate <project> --type character --source CHAR-0008 --target CHAR-0002 --note "确认不是同一角色"
python scripts/manage_entities.py set <project> --type character --target CHAR-0002 --field story_role_zh --value-json '"男主"' --note "用户确认"
python scripts/manage_entities.py retract <project> --event ENT-000001 --note "撤销"
```

归并不回写历史章节。名称、别名和旧编号均解析到目标编号；撤销后确定性恢复。

## 资产范围决定

```text
python scripts/manage_assets.py decide <project> --asset ASSET-0001 --status approved --note "确认制作"
python scripts/manage_assets.py merge <project> --asset ASSET-0004 --target ASSET-0002 --note "共用资产"
python scripts/manage_assets.py split <project> --asset ASSET-0002 --name "受损状态" --scene P12-S003 --note "单独设计"
python scripts/manage_assets.py decide <project> --asset ASSET-0007 --status excluded --note "无需制作"
python scripts/manage_assets.py retract <project> --event ASREV-000001 --note "撤销"
```

资产审核只决定范围，不产生审美设计。
