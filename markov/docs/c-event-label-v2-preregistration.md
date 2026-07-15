# 支线 C 预注册：24 小时内减弱—再增强物理波形标签 v2

冻结日期：2026-07-15  
状态：`preregistered-before-v2-label-rate-read`  
资格：`research-baseline`、`unvalidated`

## 预注册前已知信息

- [MEASURED] 旧标签固定为未来 12 h 的两段 6 h 波形。2001--2024 全热带样本中，
  2.5 m/s 阈值事件率为 1.39%，5.0 m/s 阈值为 0.13%，7.5 m/s 阈值为 0.01%。
- [MEASURED] 旧标签在强台风海上子集中，2.5 m/s 阈值事件率为 1.61%。
- [MEASURED] 新候选的开发集基准率、最终标签、验证集发生率、概率和 Brier score 在
  本文件提交时均未查看。

## 研究问题与边界

本轮寻找一个同时满足两项条件的标签：

1. [ASSUMED] 物理语义为“未来 24 h 内完整出现减弱—再增强谷形”；
2. [ASSUMED] 2001--2018 开发集的逐时次基准率位于 5%--15%。

该标签是 best-track 强度波形代理事件。它不构成 ERC 结构真值；ERC 因果标签仍需要
微波/SAR 双环结构观测。标签选择和 naive 概率基线属于统计测量，本轮不训练分类器，
也不输出巴威预测。

## 数据与固定样本

- [CITED] NOAA/NCEI IBTrACS Western Pacific v04r01。
- [ASSUMED] 复用 v1 过滤：2001--2024、`TRACK_TYPE=main`、`USA_AGENCY=jtwc_wp`、
  `USA_WIND>0`、USA `IFLAG` 为 `O/V`、`NATURE=TS`、UTC 00/06/12/18。
- [CITED] `USA_WIND` 是 1 分钟平均风，原生单位 kt；固定换算
  `1 kt=0.514444 m/s`。每一步均打印平均窗口。
- [ASSUMED] 每个候选时次必须有同一 `SID` 的精确九点序列
  `t-24,t-18,...,t,...,t+18,t+24`，相邻点均恰好 6 h。九点完整规则使所有候选共享
  完全相同的基础行。
- [ASSUMED] 物理域固定为 `V_t >= 33 m/s`，且候选未来时域内每个点
  `DIST2LAND > 0 km`。该规则约束成熟海上热带气旋，并排除未来窗口内的过陆地波形。
- [ASSUMED] 开发集按 `SEASON=2001--2018`；密封验证集按 `SEASON=2019--2024`。
  同一 `SID` 只能进入一个时间分区。

## 候选标签

候选时域固定为 `H in {12,18,24} h`，幅度阈值固定为
`q in {2.5,3.0,4.0,5.0} m/s`。

令未来 6 h 网格为 `T_H={t,t+6,...,t+H}`。标签定义为：

\[
Y_t(H,q)=1 \Longleftrightarrow
\exists\ i<j<k,\quad
V_i-V_j\ge q,\quad V_k-V_j\ge q,
\quad i,j,k\in T_H.
\]

历史持续性特征使用镜像过去时域 `P_H={t-H,...,t}` 和相同 `(H,q)`：

\[
H_t(H,q)=1 \Longleftrightarrow
\exists\ i<j<k,\quad
V_i-V_j\ge q,\quad V_k-V_j\ge q,
\quad i,j,k\in P_H.
\]

这一定义允许谷底位于时域内任一 6 h 点，并要求减弱段与恢复段均达到阈值。

## 标签选择算法

只使用 2001--2018 开发集，按以下确定性顺序选择：

1. 计算 12 个候选的逐行事件率，并标记是否位于闭区间 `[0.05,0.15]`。
2. 在达标候选中最小化 `abs(rate-0.10)`。
3. 并列时优先较大 `q`，再优先较短 `H`。
4. 若 12 个候选均未达标，标签设计判为失败；仍公布最接近 10% 的候选作为诊断，
   该候选不获得“可用标签”资格。

[MEASURED-DESIGN] 同时保存每个候选开发集事件向量的 SHA-256。若不同数值阈值产生
完全相同事件向量，报告将其列为量化等价标签；不得把重复标签解释成独立敏感性证据。

选定 `(H,q)` 后立即冻结，并原样应用到 2019--2024 验证集。验证集事件率偏离
5%--15% 时直接报告；标签不得回调。

## Naive 概率基线

使用开发集估计两个概率，再固定应用于验证集：

\[
p_{clim}=\frac{N_1+0.5}{N+1},
\qquad
p_{pers}(Y=1\mid H=h)=\frac{N_{1,h}+0.5}{N_h+1}.
\]

- [ASSUMED] Jeffreys 0.5/0.5 平滑只防止零概率。
- [MEASURED-DESIGN] 气候基线含 1 个经验概率参数；持续性基线含 2 个条件概率参数；
  标签选择含 2 个离散超参数 `(H,q)`，只在开发集选择一次。
- [CITED] 主评分为验证集 Brier score。报告
  `Brier_persistence-Brier_climatology` 和
  `1-Brier_persistence/Brier_climatology`。
- [ASSUMED] 95% CI 按验证集 `SID` 整块 bootstrap 2,000 次，随机种子 `20260715`。
- [MEASURED-DESIGN] 可靠性表按验证集实际预测概率分组，打印预测概率、观察率、行数、
  台风数和事件数。

非退化评分要求验证集同时含事件与非事件。持续性基线的两个训练分层都要有数据；若
两个条件概率相同到小数点后 6 位，报告“持续性退化为常数概率”。

## 证伪判据

- 开发集没有候选达到 5%--15%：证伪本轮候选网格能够建立目标基准率的命题。
- 验证集只有单一类别：Brier 比较资格关闭。
- 持续性相对气候的 Brier 差 95% CI 跨 0：当前验证集无法分辨持续性增益。
- Brier 差 95% CI 全部高于 0：持续性基线被密封验证集证伪。
- Brier 差 95% CI 全部低于 0：持续性基线在本验证集显示可分辨改进。
- 标签开发率达标、验证率显著漂移：报告标签基准率时间不稳定；保留冻结标签。

## 三把刀

1. 状态向量：本轮没有动力状态；观测向量为九点 1 分钟 best-track 强度和未来海陆标记。
2. 参数与观测：标签含 2 个开发集离散超参数；气候/持续性基线分别含 1/2 个经验概率
   参数；独立评分单位按台风计。
3. 证伪数据：2019--2024 密封风暴的标签、Brier score、可靠性和台风 block CI。

## 预注册偏离日志

运行后任何样本、候选、排序、标签、分区、概率或评分变更必须记录在此处。当前无偏离。

## 引用

- [NOAA/NCEI IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive)
- [Brier (1950), Verification of forecasts expressed in terms of probability](https://doi.org/10.1175/1520-0493(1950)078%3C0001:VOFEIT%3E2.0.CO;2)

