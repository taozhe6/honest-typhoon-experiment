# /goal — 自研台风预测项目

## 北极星
建立一套【业余、但诚实】的台风预测实验平台,同时推进三条支线:
  (A) 路径预测  (B) 强度预测  (C) 眼墙置换(ERC)结构事件预测
目标不是打败气象台,而是:
  「每一个输出的数字,都能说清它从哪来、有多不确定、拿什么能证伪它。」

## 完成的定义(达到任一即算该支线"毕业")
- A 路径:能复现并对比 ≥2 家业务模式的路径,报出自己的误差,不假装超越。
- B 强度:在【登陆时刻子集】上(唯一有测站真值的地方),报出各机构误差 + 自己的误差。
- C ERC:能在微波影像上,对巴威的 3-4 次置换,输出"未来24h内ERC概率",并给 Brier score + 可靠性图。

## 明确【不做】的(踩过的坑,不再踩)
- 不发明没有文献出处的方程。每个关系式:要么有引用,要么是拟合+交叉验证过的。
- 不输出单一确定值。输出分布/区间/概率。
- 不用 accuracy 评价稀有事件(ERC)。用 Brier score。
- 不在密封回报检验通过前,给任何分支贴 "validated"。
- 不把 IBTrACS 的 RMW 当观测用(95.8%是5海里倍数、65.8%不变 = 它是填出来的)。

## 诚实的现状(2026-07-15)
- LGEM: 归档。
- Markov v0.1: research-rejected(∂V̇/∂Z=0,增强态改不了风速)。冻结为失败基线。
- Markov v0.2: 数据工程阶段,已在微波影像中找到 26/102km 双肩结构。← 真实进展
- IBTrACS五机构: 已完成。测得分歧 2-6 m/s;ρ 不可识别(无独立真值)。

## 三把永久性的刀(每次交付自检,不通过就打回)
1. 状态向量里有什么?
2. 参数几个?独立观测几个?(加一个环 → 必须加一个独立观测)
3. 拿什么数据证伪它?

## 平台原则
- 三条支线【并行】,不强制先后。业余项目,允许哪条有兴致就推哪条。
- 每条支线都要能【独立出一个小成果】,不必等其他支线。
- 缺口要标注,但每次交付必须同时列出"已经能用的东西",不许只报问题。 写入对应项目的文档

## 2026-07-15 执行索引

上方 `/goal` 保持原文。当前完整进展、数值、资格判决、三把刀审计和复现命令见 [阶段总报告](PROJECT_REPORT_2026-07-15.md)。

- [A 路径发布报告](path-track-benchmark/report_round_v2.md)
- [A 路径 DYC2/UKMET 深化报告](path-track-benchmark/report_round_v3.md)
- [DYC2 来源审计](path-track-benchmark/dyc2_source_audit.md)
- [B 登陆强度与风压报告](ibtracs-agency-disagreement/report_b_branch.md)
- [C 零人工标签与 ERC 资源报告](markov/report_c_branch.md)
- [C 一手微波覆盖纠错报告](markov/report_c_coverage_correction.md)
- [C 事件标签 v2 与非退化 Brier 基线](markov/report_c_event_label_v2.md)
- [FAST 固定常量敏感性报告](markov/report_global_sensitivity.md)
- [`theta=Ck/h` 终值传播报告](markov/report_theta_propagation.md)
- [西北太平洋强度可预报性的三重天花板](INTENSITY_PREDICTABILITY_CEILINGS.md)
- [统一验证脚本](scripts/verify_all.sh)

## 证据宪法

- 一手观测可以确证景级结构，也可以给出不可判定。
- 物理缺席结论必须先证明时间覆盖、空间覆盖、采样密度和观测算子足以看见目标现象。
- 二手叙述只提供待核线索，单独列示，不进入一手确证或否定 tally。
- 连续双环观测时段属于采样记录；完整 ERC 周期数需要形成、收缩、内环消失和周期边界证据。
