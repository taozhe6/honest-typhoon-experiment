# 自研台风实验平台：2026-07-15 阶段总报告

项目资格：`research / learning reproduction`。全部分支继续服从根目录 `/goal`；最高状态分别为 `unvalidated`、`research-measured`、`research-baseline` 和 `research-rejected`。

## 这轮做成了什么

|工作流|可独立使用的成果|当前资格|北极星毕业闸门|
|---|---|---|---|
|A 路径|26 个强台风、2 家业务模式、1,203 个严格配对预报、固定等权共识、聚类区间与留一台风覆盖率|`learning-reproduction / unvalidated`|已达到“复现并比较至少两家”的学习性标准|
|B 登陆强度|108 次五家齐全登陆的独立真值审计、CMA 代理误差表、可复用风压诊断与 Pc-only 留出检验|`research-measured`|独立测站/雷达真值覆盖 0/108，真实误差闸门保持关闭|
|C 强度波动/ERC|12,190 个自动波形窗口、风暴折外 Brier 基线、公开 CE/ERC 资源表、巴威 16 景 SAR `V·R` 时序|`research-baseline`|逐行 ERC onset 真值与 24 h ERC 概率仍待建立|
|固定常量审计|`Ck`、边界层深度、FAST `kappa` 各 `+/-30%`，三个合成场景、两套引擎、结构控制|`synthetic-structural-audit`|确认 `Ck/h` 混淆与 `regime -> V` 零耦合，v0.1 保持淘汰|

[MEASURED] Git 已包含完整历史，失败基线永久标签为 `v0.1-rejected-baseline`。本轮发布链为 A `05febfc`、B `0f0bc29`、C `9aeed7d`、固定常量审计 `c468863`。

## 基础测量：五机构强度分歧

[五机构测量报告](ibtracs-agency-disagreement/report.md)先回答了平台的数据底座问题：

- [MEASURED] 2015-2024 complete-five 样本含 4,234 个时次、232 个台风；Kish 有效台风数为 162。
- [MEASURED] 统一到 10 分钟后，成对 `sd(V_i-V_j)` 为 `2-5 m/s`（JTWC 系数 0.88）和 `2-6 m/s`（系数 0.93）。
- [MEASURED] 留一偏差严格零和，使交换相关公式产生超出五家解释上限的 `n_eff`；有限 `n_eff` 被预注册闸门判为不可识别。
- [MEASURED] 控制强度、年代和生消阶段后，整体分歧在靠岸过程中增大；400 km 折点缺少预注册支持。
- [MEASURED] 五家原始值齐全率为 37.8%，KMA 覆盖率为 39.5%；缺测交集将主支持期压缩到 2015-2024。

该测量描述机构一致性。五家共同偏离真实强度的程度需要独立真值。

## A 路径：业务模式复现

发布物：[A 支线路径报告](path-track-benchmark/report_round_v2.md)与[误差图](path-track-benchmark/outputs/round_v2/error_vs_lead.png)。

### 已经能用的东西

- [MEASURED] 冻结规则机械纳入 26/27 个合资格强台风，生成 1,203 个 CMC/NGX/DYC2 同风暴、同循环、同有效时刻案例。
- [CITED] CMC 与 NGX 使用原始 late-cycle 6 小时 TECH；NGX 是 Navy 模式路径配 GFS tracker，CMC 的动力模式本体独立。
- [ASSUMED] DYC2 是 CMC/NGX 的 0.5/0.5 单位球平均，拟合参数为 0。

|时效|CMC 平均误差 km|DYC2 平均误差 km|NGX 平均误差 km|
|---:|---:|---:|---:|
|24 h|83 [73, 93]|71 [62, 81]|89 [79, 100]|
|48 h|158 [139, 180]|126 [108, 146]|152 [134, 172]|
|72 h|250 [219, 282]|204 [177, 233]|241 [211, 273]|
|96 h|341 [294, 397]|282 [246, 325]|319 [280, 368]|
|120 h|470 [401, 549]|398 [346, 460]|439 [373, 516]|

[MEASURED] DYC2 在 5/5 个时效低于当时效最佳单模；其中 4/5 个差值的台风聚类 95% CI 完全低于 0。120 h 的 `DYC2-NGX=-40 km`，区间 `[-93,+7] km`，证据仍跨零。

[ASSUMED+MEASURED] 移除各 TECH 的时效均值后，误差相关 `rho=0.36` [0.23, 0.49]；交换相关假设给出 `n_eff=1.47` [1.34, 1.62]。该量衡量误差一致性。

[MEASURED] 留一台风 80% 经验半径的实际覆盖率在 24-120 h 均约 80%，各聚类区间包含目标 0.80。

### 缺口与下一步

- 历史 a-deck 缺少逐产品真实公开时刻，当前资格保持学习性复现。
- IBTrACS USA 位置属于事后分析中心，位置真值也带分析误差。
- 下一阶段需保存真实 `available_at`，扩展模式与年份，再开展前瞻评分。

## B 登陆强度与风压诊断

发布物：[B 支线报告](ibtracs-agency-disagreement/report_b_branch.md)、[风压图](ibtracs-agency-disagreement/outputs/b_branch/wind_pressure_diagnostic.png)与 [`wind_pressure.py`](ibtracs-agency-disagreement/src/ibtracs_measurement/wind_pressure.py) 可复用模块。

### 已经能用的东西

- [MEASURED] 108 次五家齐全登陆中，当前 NCEI/CMA 公开输入包提供的独立测站或雷达真值为 0 次；真实 MAE、RMSE 与真误差相关矩阵标记为 `unidentifiable`。
- [MEASURED+ASSUMED] 10 分钟统一口径的 CMA 参照代理差显示：JTWC/JMA/HKO/KMA 的 MAE 约为 `3/3/2/3 m/s`，RMSE 约为 `4/4/3/4 m/s`。这些量共享 CMA 参照，CMA 具有中国测站资料主场优势。
- [ASSUMED] 风压式含 2 个拟合参数：`V_1min = alpha + beta*(1010-Pc)`。
- [MEASURED] 18,707 条、672 个台风得到 `alpha=8.95` [8.72, 9.16] m/s，`beta=0.66` [0.66, 0.67] m/s/hPa，`corr(V,Pc)=-0.98`，台风聚类区间 [-0.98, -0.98]。
- [MEASURED] Pc-only 留出台风五折 RMSE 为 `3 m/s` [3, 3]，MAE `2 m/s`，P95 绝对误差 `7 m/s`；相对训练均值基线的方差削减为 96% [96%, 97%]。

Pc 对同一事后分析体系内的 V 具有强替代信息。独立准确性增益需要观测误差模型。

### 缺口与下一步

- B 的毕业数据需要事件级测站/雷达表，字段至少包含位置、时刻、平均窗口、持续风与质量标志。
- 点测站风、近岸持续风和中心最大风属于不同观测算子，接入时需保持语义分离。
- 平台自身强度模型仍处于淘汰/数据工程状态，因此“自己的登陆真误差”尚无合格候选可评分。

## C 强度波动与 ERC 结构

发布物：[C 支线报告](markov/report_c_branch.md)、[可靠性图](markov/outputs/c_branch/event_reliability_primary.png)、[巴威 `V·R` 图](markov/outputs/c_branch/bavi_cyclobs_vr_timeline.png)与 Kuo et al. 62 行 CE formation 表。

### 已经能用的东西

- [MEASURED] IBTrACS 2001-2024 的 631 个台风形成 12,190 个完整五点窗口；主事件由 JTWC 1 分钟 best-track 自动计算。
- [ASSUMED] 主标签要求未来 12 h 内先下降至少 5 m/s、再回升至少 5 m/s。
- [MEASURED] 主阈值产生 16 个事件，行发生率 `0.131%` [0.074%, 0.196%]；风暴发生率 `2.54%` [1.43%, 3.80%]。
- [MEASURED] 气候基线 Brier 为 `0.001311` [0.000737, 0.001957]；持续性为 `0.001313` [0.000738, 0.001960]。配对差 `+0.00000204` [0.00000111, 0.00000309]，持续性增益被否定。
- [MEASURED] 公开资源审计找回 62 行、55 个台风的 WNP CE formation 表；现代逐行 WNP ERC-onset 表在冻结范围内仍为空缺。
- [MEASURED] 巴威共有 16 景 SAR、12 景质量合格、4 景满足双峰阈值；四景连续构成 1 个双环风结构观测时段。旧版对“3-4 次置换”的物理否定已经撤回：7 月 4 日景缺少眼区，7 月 7 日两景未通过中心质量/眼区门槛，两个二手待核窗口均不可判定。详见[覆盖纠错报告](markov/report_c_coverage_correction.md)。

### 缺口与下一步

- 自动强度波形标签只描述结果形状，物理原因字段为空；环境变化与中心分析跳变可以产生同形波动。
- 巴威 SAR 的离散采样尚未闭合 ERC 生命周期。
- 下一阶段使用 TC PRIMED 85-92 GHz 自动 ring-score 建立结构通道，再以风暴分组 Brier 和可靠性检验 24 h ERC 概率。

## FAST 固定常量审计

发布物：[固定常量敏感性报告](markov/report_global_sensitivity.md)与[敏感性图](markov/outputs/global_sensitivity/global_sensitivity_wind.png)。

### 已经能用的东西

- [CITED] 冻结常量为 `Ck=1.2e-3`、WP `h=1800 m`、`kappa=0.10`，来源固定到上游实现 commit `a540a1e`。
- [MEASURED] `Ck +/-30%` 的三个场景最大 48 h 风速变化为 `2.53-3.54 m/s`；`h +/-30%` 为 `2.60-3.41 m/s`；`kappa +/-30%` 为 `0.77-0.83 m/s`。
- [MEASURED] `Ck` 与 `h` 同比 `+/-30%` 后，最大原生状态差 `2.55e-11`，最大转移概率 L1 差 `8.66e-16`。
- [MEASURED] `Ck/h` 是当前方程可识别的组合量。把 `Ck` 和 `h` 同时作为独立自由度会重复计算一个结构方向。
- [MEASURED] 固定 regime 与完整 Markov 的最大风速路径差为 `0.0 m/s`。离散 regime 分叉可将气压响应放大到 `4.8 hPa`，风速仍保持同一路径。

### 缺口与下一步

- 合成场景承担结构探针，现实预报误差仍需密封回报样本。
- 下一版应暴露 `theta_FAST=Ck/h`，并用独立边界层/通量观测决定能否拆分 `Ck` 与 `h`。
- regime 与风速条件分布的耦合需要新增独立结构观测和重新计算的参数预算。

## 三把刀总审计

|工作流|状态向量/记录向量|参数与独立观测|证伪通道|
|---|---|---|---|
|A|每时次 `(lat,lon)`|DYC2 拟合参数 0；两个相关模式输入|同循环 IBTrACS 事后路径、配对误差、留一台风覆盖|
|B|`(V_1min,Pc)`；登陆五家 10 分钟风速|风压式 2 参数；672 个风暴聚类；登陆独立真值 0/108|留出台风 Pc-only 误差；未来测站/雷达事件表|
|C|五点强度窗口；SAR `(V,R,peaks,prominence)`|气候/持续性每折 1/2 个经验概率；631 个独立风暴|折外 Brier、公开标签审计、质量合格 SAR 序列|
|Markov v0.1|`(V,m,Pc,RMW,Z)`|2 个未拟合 demo 参数；`Ck/h` 只值一个结构方向|`dV/dt` 对 `Z` 的零导数、比值保持扰动、未来密封回报|

## 预注册与偏离

- [MEASURED] A 的规则、资格 manifest、误差计算按 `8528a77 -> 52199e3 -> 05febfc` 分阶段提交。
- [MEASURED] B 的协议先于结果提交于 `46a43a9`；公开真值覆盖为零后按协议终止 Tier 2 匹配。
- [MEASURED] C 的协议与参数预算修订先于结果提交于 `8649c15` 和 `a40c3ab`；四项网页/下载偏离均在分支报告逐项登记。
- [MEASURED] 固定常量场景与扰动先于结果提交于 `18f0c02`；运行偏离为 0。
- [MEASURED] 根目录 `/goal` 原文保持逐字不变，本报告只追加执行状态与证据链接。

## 复现与验证

统一验证入口：

```bash
cd "/Users/taozhe/Documents/New project/typhoon"
./scripts/verify_all.sh
```

首次重建三个隔离环境：

```bash
cd "/Users/taozhe/Documents/New project/typhoon"
python3 -m venv path-track-benchmark/.venv
path-track-benchmark/.venv/bin/python -m pip install -r path-track-benchmark/requirements.txt
python3 -m venv ibtracs-agency-disagreement/.venv
ibtracs-agency-disagreement/.venv/bin/python -m pip install -r ibtracs-agency-disagreement/requirements.txt
python3 -m venv markov/.venv
markov/.venv/bin/python -m pip install -r markov/requirements-research.txt
```

[MEASURED] 2026-07-15 最终验证通过 A 15 项、B 18 项、Markov/C/敏感性 55 项，共 88 项测试；三套环境均使用项目内 `.venv`。`compileall` 与 `git diff --check` 同时通过。

## 项目当前边界

A 已形成可发布的学习性路径对比。B 已把公开真值缺口量化为 0/108，并提供代理测量与风压工具。C 已建立零人工标签概率门槛和一段可核查的巴威双环证据。Markov v0.1 已成为可复现的失败基线，v0.2 的推进条件明确落在独立结构观测上。
