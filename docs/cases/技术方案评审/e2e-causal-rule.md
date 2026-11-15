# 技术方案与目标因果关联分析规则

- 类型：`bmm:TextLogicOperationalRule`
- 所属策略：`技术方案合理性评审`
- 执行级别：建议
- 适用范围：总体方案

## 检查目标
检查方案中对“方案支持/实现目标”的主张是否具有从 `bmm:Means` 或子类到 `bmm:DesiredResult` 或子类的完整论证链，并确认没有命中错误的因果模式。

## 输入
- claim：文档中的因果主张
- means：方案/手段
- desired_result：目标/预期成果
- premises：论证链前提
- chain：手段到结果的逻辑链
- patterns：领域库中的 active 因果模式

## 输出
- verdict：符合 / 不符合 / 不适用 / 无法判定
- matched_pattern
- basis：前提与论证链依据
- missing_premises
- human_confirmation_required

## 约束
- 不以观测阈值替代逻辑前提；评审不是评测。
- 缺前提记无法判定，不直接判不符合。
- 命中错误因果模式才判不符合。
- 未命中 active pattern 时允许 LLM 提出候选模式，但必须人工确认后才能转为 active。
