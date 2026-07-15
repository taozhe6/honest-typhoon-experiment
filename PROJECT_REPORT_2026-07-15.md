# 自研台风实验平台：2026-07-15 阶段总报告

项目资格：`research / learning reproduction`。全部分支继续服从根目录 `/goal`；最高状态分别为 `unvalidated`、`research-measured`、`research-baseline` 和 `research-rejected`。

## 这轮做成了什么

|工作流|可独立使用的成果|当前资格|北极星毕业闸门|
|---|---|---|---|
|A 路径|26 个强台风的 CMC/NGX 复现；DYC2 来源审计；17 个台风的独立 UKMET 核心敏感性|`learning-reproduction / unvalidated`|已达到“复现并比较至少两家”的学习性标准|
|B 登陆强度|108 次五家齐全登陆的独立真值审计、CMA 代理误差表、可复用风压诊断与 Pc-only 留出检验|`research-measured`|独立测站/雷达真值覆盖 0/108，真实误差闸门保持关闭|
|C 强度波动/ERC|一手覆盖纠错；5--15% 标签 v2；密封验证集非退化 Brier 基线；巴威 16 景 SAR `V·R` 时序|`research-baseline / unvalidated`|逐行 ERC onset 真值与 24 h ERC 概率仍待建立|
|固定常量审计|三个常量各 `+/-30%`；可识别 `theta=Ck/h` 的 61 点、48 h 终值传播|`synthetic-structural-audit / unvalidated`|确认 `Ck/h` 混淆与 `regime -> V` 零耦合，v0.1 保持淘汰|

[MEASURED] Git 已包含完整历史，失败基线永久标签为 `v0.1-rejected-baseline`。
本轮深化发布链为 C 覆盖纠错 `dfcce6b`、A v3 `454629a`、C 标签 v2
`17361ba`、`theta` 传播 `c225ca5`、三重天花板 `a84fdc3`。

## 基础测量：五机构强度分歧

[五机构测量报告](ibtracs-agency-disagreement/report.md)先回答了平台的数据底座问题：

- [MEASURED] 2015-2024 complete-five 样本含 4,234 个时次、232 个台风；Kish 有效台风数为 162。
- [MEASURED] 统一到 10 分钟后，成对 `sd(V_i-V_j)` 为 `2-5 m/s`（JTWC 系数 0.88）和 `2-6 m/s`（系数 0.93）。
- [MEASURED] 留一偏差严格零和，使交换相关公式产生超出五家解释上限的 `n_eff`；有限 `n_eff` 被预注册闸门判为不可识别。
- [MEASURED] 控制强度、年代和生消阶段后，整体分歧在靠岸过程中增大；400 km 折点缺少预注册支持。
- [MEASURED] 五家原始值齐全率为 37.8%，KMA 覆盖率为 39.5%；缺测交集将主支持期压缩到 2015-2024。

该测量描述机构一致性。五家共同偏离真实强度的程度需要独立真值。

## 综合成果：强度可预报性的三重天花板

发布物：[综合报告](INTENSITY_PREDICTABILITY_CEILINGS.md)、
[机器证据包](outputs/intensity_predictability_ceilings/synthesis.json)与
[三联图](outputs/intensity_predictability_ceilings/three_ceilings.png)。

### 已经能用的东西

- [MEASURED] 真值层：108 次五家齐全登陆的独立测站/雷达真值覆盖为 `0/108`；
  五家真实 MAE、RMSE 与真误差相关保持不可识别。
- [ASSUMED→MEASURED] 冗余层：统一风窗后的成对机构分歧为 `2--6 m/s`；
  五机构强度 `n_eff` 因留一偏差严格零和而不可识别。
- [MEASURED] 纠错：`1.46、1.47、1.46` 属于同一观测审计的重复引用与路径误差
  构念混合，无法称为三次独立复现。独立重复证据数为 0。
- [ASSUMED→MEASURED] 参数层：`theta=Ck/h` 的 `[0.7,1.3]` 61 点情景传播后，
  最大 48 h 单侧风速变化为 `3.54 m/s`，端点总宽度最大 `6.07 m/s`。
- [MEASURED] 合成参数响应与机构分歧位于同一数量级；两者使用独立统计语义，
  方差相加资格为零。

### 缺口与下一步

- “天花板”表示当前证据瓶颈，未声明大气内在可预报性的定理上界。
- 第一优先级仍是带平均窗、位置、时刻和质量标志的事件级独立登陆真值。
- `theta` 的 `+/-30%` 属于压力测试边界；独立通量/边界层观测才能赋予概率分布。

## A 路径：业务模式复现

发布物：[A v2 路径报告](path-track-benchmark/report_round_v2.md)、
[DYC2 来源审计](path-track-benchmark/dyc2_source_audit.md)、
[A v3 UKMET 报告](path-track-benchmark/report_round_v3.md)与
[v3 误差图](path-track-benchmark/outputs/round_v3/error_vs_lead.png)。

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

[MEASURED] DYC2 深审计发现原始 a-deck 中官方 `DYC2` TECH 行数为 0；项目里的
`DYC2` 全部由 CMC 与 NGX 以 0.5/0.5 单位球平均本地生成。v3 将它重命名为
`LOCAL_EQ2_CMC_NGX`，历史别名仅用于溯源。

[CITED] UKM 是 UK Met Office Unified Model 经发展追踪器产生的独立业务 TECH。
[MEASURED] 严格同样本含 552 行、17 个台风；2023 年 UKM 覆盖为 0。

|相关诊断|CMC vs NGX|LOCAL_EQ2 vs UKM|差值|
|---|---:|---:|---:|
|lead-centered rho|0.42 [0.26, 0.58]|0.35 [0.13, 0.53]|NA|
|`n_eff`|1.41 [1.26, 1.58]|1.49 [1.31, 1.77]|+0.07 [-0.20, +0.40]|

[MEASURED] 独立 UKMET 核心的点估计更接近 2，增量区间跨 0；预注册的“接近 2”
判据未获支持。UKM 在五个时效的平均误差点估计均低于本地共识，只有 24 h
配对差区间完全排除 0：`LOCAL_EQ2-UKM=+11 km` [3, 19]。

### 缺口与下一步

- 历史 a-deck 缺少逐产品真实公开时刻，当前资格保持学习性复现。
- IBTrACS USA 位置属于事后分析中心，位置真值也带分析误差。
- 2023 年 UKM 空缺使 v3 支持范围缩为 2022 与 2024；17 个台风限制增量精度。
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

发布物：[C v1 失败基线](markov/report_c_branch.md)、
[一手覆盖纠错](markov/report_c_coverage_correction.md)、
[C 标签 v2 报告](markov/report_c_event_label_v2.md)、
[v2 可靠性图](markov/outputs/c_event_label_v2/validation_reliability.png)与
[巴威一手覆盖图](markov/outputs/c_coverage_correction/bavi_primary_coverage_timeline.png)。

### 已经能用的东西

- [MEASURED] v1 的 12 h / 5 m/s 标签发生率只有 `0.131%`，作为稀有、退化失败基线保留。
- [ASSUMED] v2 在开发期枚举 `H={12,18,24} h`、`q={2.5,3,4,5} m/s`，
  目标发生率冻结为 5--15%；事件要求未来先下降、后恢复且两段均达到 `q`。
- [MEASURED] 冻结选择为 `H=24 h, q=2.5 m/s`。开发期 2001--2018 为
  `5.9%` [4.7%, 7.2%]，密封验证期 2019--2024 为 `14.4%`
  [9.9%, 19.4%]，777 行、70 个台风且正负类齐全。
- [MEASURED] 验证期气候 Brier 为 `0.130603` [0.091158, 0.174213]；
  持续性为 `0.131092` [0.091293, 0.175110]；配对差
  `+0.000489` [0.000001, 0.001125]，持续性略差。
- [MEASURED] USA_WIND 全部是 5 kt 倍数，12 个数值候选只形成 6 个不同事件向量；
  `q=3/4/5` 在每个时效内完全等价。开发到验证的发生率点变化为
  `+8.5` 个百分点，两个台风聚类 CI 不重叠。
- [MEASURED] 巴威共有 16 景 CyclObs SAR、12 景质量合格、4 景满足双峰阈值；
  四景连续构成 1 个双环风结构观测时段。7 月 4 日景缺少眼区，7 月 7 日两景
  未通过中心质量/眼区门槛，两个二手待核窗口均不可判定。TC PRIMED 在审计时
  对 WP09/2026 的 preliminary 文件数为 0。
- [CITED] NASA/Wikipedia/CIMSS 叙述仅作为待核线索单列，未进入一手确证 tally。

### 缺口与下一步

- v2 只描述强度波形，ERC 因果字段为空；环境变化与中心分析跳变可以产生同形波动。
- 阈值量化严重，开发/验证发生率发生明显时变；当前资格保持 `unvalidated`。
- 巴威 SAR 的离散采样尚未闭合 ERC 生命周期；7 月 4 日和 7 日仍属存疑。
- 下一阶段先执行双人盲标与结构基准率审计，再检验 24 h ERC 概率。

## FAST 固定常量审计

发布物：[固定常量敏感性报告](markov/report_global_sensitivity.md)、
[`theta` 终值传播报告](markov/report_theta_propagation.md)、
[常量敏感性图](markov/outputs/global_sensitivity/global_sensitivity_wind.png)与
[`theta` 终值图](markov/outputs/theta_propagation/theta_final_wind.png)。

### 已经能用的东西

- [CITED] 冻结常量为 `Ck=1.2e-3`、WP `h=1800 m`、`kappa=0.10`，来源固定到上游实现 commit `a540a1e`。
- [MEASURED] `Ck +/-30%` 的三个场景最大 48 h 风速变化为 `2.53-3.54 m/s`；`h +/-30%` 为 `2.60-3.41 m/s`；`kappa +/-30%` 为 `0.77-0.83 m/s`。
- [MEASURED] `Ck` 与 `h` 同比 `+/-30%` 后，最大原生状态差 `2.55e-11`，最大转移概率 L1 差 `8.66e-16`。
- [MEASURED] `Ck/h` 是当前方程可识别的组合量。把 `Ck` 和 `h` 同时作为独立自由度会重复计算一个结构方向。
- [MEASURED] 固定 regime 与完整 Markov 的最大风速路径差为 `0.0 m/s`。离散 regime 分叉可将气压响应放大到 `4.8 hPa`，风速仍保持同一路径。
- [ASSUMED→MEASURED] `theta/theta_0=[0.7,1.3]` 的 61 点网格已传播到 48 h
  终值；最大单侧变化 `3.54 m/s`，最大端点宽度 `6.07 m/s`。
- [MEASURED] `Ck` 缩放与等价 `h` 反向缩放的完整轨迹最大差为 `3.55e-15`。

### 缺口与下一步

- 合成场景承担结构探针，现实预报误差仍需密封回报样本。
- 当前输出已经暴露 `theta_FAST=Ck/h`；独立边界层/通量观测仍负责决定能否拆分 `Ck` 与 `h`。
- `+/-30%` 只承担有界压力测试，概率分布语义为空。
- regime 与风速条件分布的耦合需要新增独立结构观测和重新计算的参数预算。

## 三把刀总审计

|工作流|状态向量/记录向量|参数与独立观测|证伪通道|
|---|---|---|---|
|A|每时次 `(lat,lon)`|`LOCAL_EQ2` 拟合参数 0；CMC/NGX 两输入；UKM 独立核心敏感性|同循环 IBTrACS 事后路径、配对误差、台风聚类 `n_eff`|
|B|`(V_1min,Pc)`；登陆五家 10 分钟风速|风压式 2 参数；672 个风暴聚类；登陆独立真值 0/108|留出台风 Pc-only 误差；未来测站/雷达事件表|
|C|9 点强度窗口；SAR `(V,R,peaks,prominence,eye_coverage)`|标签拟合参数 0；开发期选 1 个 `(H,q)`；验证 70 个台风|密封 Brier、量化审计、一手确证/存疑/无覆盖分栏|
|Markov v0.1|`(V,m,Pc,RMW,Z)`|2 个未拟合 demo 参数；只扫描 1 个可识别 `theta=Ck/h`|`dV/dt` 对 `Z` 的零导数、61 点终值传播、未来密封回报|

## 预注册与偏离

- [MEASURED] A 的规则、资格 manifest、误差计算按 `8528a77 -> 52199e3 -> 05febfc` 分阶段提交。
- [MEASURED] A v3 的 DYC2 审计与 UKM 设计先于结果冻结于 `577e210`，结果发布于 `454629a`。
- [MEASURED] B 的协议先于结果提交于 `46a43a9`；公开真值覆盖为零后按协议终止 Tier 2 匹配。
- [MEASURED] C 的协议与参数预算修订先于结果提交于 `8649c15` 和 `a40c3ab`；四项网页/下载偏离均在分支报告逐项登记。
- [MEASURED] C 覆盖纠错发布于 `dfcce6b`；事件标签 v2 设计先于结果冻结于 `80e50fe`，结果发布于 `17361ba`。
- [MEASURED] 固定常量场景与扰动先于结果提交于 `18f0c02`；运行偏离为 0。
- [MEASURED] `theta` 专用协议先于网格输出冻结于 `9b5759e`；结果发布于 `c225ca5`。
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

[MEASURED] 2026-07-15 本轮最终验证通过 A 22 项、B 18 项、
Markov/C/敏感性 77 项，共 117 项测试；三套环境均使用项目内 `.venv`。
三重天花板证据陈旧性与 manifest 哈希检查、`compileall`、`git diff --check`
同时通过。

## 项目当前边界

A 已形成 CMC/NGX 复现、DYC2 来源纠错和 UKMET 独立核心敏感性。
B 已把公开真值缺口量化为 0/108，并提供代理测量与风压工具。
C 已建立非退化强度波形 Brier 门槛和分级明确的巴威一手双环证据。
Markov v0.1 是可复现失败基线；`theta=Ck/h` 已传播到最终强度输出；
v0.2 的推进条件落在独立微波结构观测和密封回报评分上。
