# A 支线路径对比：扩展样本前瞻性修订

冻结日期：2026-07-15  
状态：`prospective-amendment-before-expanded-sample-results`  
已知结果边界：冻结时已经看过 round v1 四场台风的 `CMC/NGX` 路径误差。尚未生成
2022--2024 全量资格名单，尚未计算扩展样本的任何路径误差、误差相关、`n_eff`、
球面共识误差或交叉验证覆盖率。因此 round v2 属于前瞻性扩展样本分析，同时明确
承认四场重叠个例带来的先验知识。

## A1. 模式独立性与相关性判据

- [CITED] `CMC` 是加拿大环境部全球动力模式。
- [CITED] `NGX` 是 NAVGEM/NOGAPS 路径，并使用 GFS vortex tracker。`GFS tracker`
  表示中心追踪算法，不能解释为 GFS 动力核心。CMC 与 NAVGEM/NOGAPS 的动力模式
  本体不同；共同观测、资料同化来源和追踪算法仍可产生相关误差。
- [ASSUMED] 两套路径在统计上可能相关，本项目把它们视为两个输入源，并单独测量
  其有效独立意见数。

主相关量使用严格配对案例的径向路径误差。先分别减去每个模式在各预报时效的均值，
再计算 lead-centered Pearson `rho`；这一步移除路径误差随提前量共同增长的机械信号。
同时报告原始 Pearson、lead-centered Spearman，全部用台风 block bootstrap 2,000 次
给 95% CI，随机种子 `20260715`。

在明确的交换相关假设下：

\[
n_{eff}=\frac{2}{1+\rho}.
\]

主 `n_eff` 使用 lead-centered Pearson `rho`，原始 Pearson 结果作为敏感性。这个数字
衡量两套模式误差的一致性，不能证明动力独立，也不能衡量准确性。若主 `n_eff < 1.25`，
报告正文必须写明“这两套路径在该样本中只提供约一个有效独立意见”。

## A2. TECH 与时间口径

- [CITED] UCAR/NHC 将 late guidance 向前调整到当前循环的版本标为末尾 `I` 的 TECH。
- [MEASURED-DESIGN] 本轮读取精确 TECH `CMC` 和 `NGX`，两者末尾均无 `I`。
- [ASSUMED] 两者均按归档中的原始 late-cycle 循环评分；不读取 `CMCI/NGXI`，不把
  前一循环向前调整，也不对模式位置做时间插值。
- [MEASURED-DESIGN] 只使用 a-deck 原生 6 小时倍数提前量，主时效为
  24/48/72/96/120 h。验证位置要求 IBTrACS 完全相同有效时刻。

历史归档缺少每条指导的真实公开时刻；成果资格保持 `learning-reproduction`，不能标为
实时业务可用性验证或 `validated`。

## A3. 台风选择规则

在读取扩展样本误差前冻结以下机械规则，满足者全部纳入：

1. [MEASURED-DESIGN] IBTrACS v04r01，海盆 `WP`，季节 2022、2023、2024，
   `USA_ATCF_ID` 序号为 `01--49`；投资区 `90--99` 排除。
2. [CITED] “峰值达到强台风级”按 CMA 原生 2 分钟平均风定义：任一时次
   `CMA_WIND * 0.514444... >= 41.5 m/s`。`CMA_WIND` 缺失的风暴不满足资格，并进入
   缺测表。
3. [MEASURED-DESIGN] 同一风暴至少存在一个共同起报循环，使 `CMC` 和 `NGX` 都有
   原始 `tau=72 h` 可解析位置。资格判断只看字段存在与质控，不读取误差大小。
4. [MEASURED-DESIGN] 资格名单先写入带来源哈希的 manifest 并提交版本控制，随后
   才运行误差计算。主分析仍取两模式与 exact-time best track 的严格配对交集。

## 球面等权共识与交叉验证

冻结的 `DYC2` 继续使用原预注册第 6 节单位球向量平均，权重固定为 0.5/0.5，拟合参数
为 0。每个时效报告三者在完全相同案例上的均值、中位数、P80 和台风 block bootstrap
95% CI；同时报告 `DYC2-CMC` 与 `DYC2-NGX` 配对差。

不确定性采用留一台风交叉验证：每次用其余台风的 `DYC2` 径向误差估计 50/80/95%
经验半径，再检验留出台风覆盖率。每个时效训练台风少于 10 个时标记
`insufficient-training-storms`。区间按台风重采样，不能把同一台风相邻循环当成独立样本。

## 证伪判据

- `DYC2` 相对最佳单模的配对误差差 95% CI 全部高于 0：证伪“简单等权共识改善路径”。
- `DYC2` 与最佳单模配对差 CI 包含 0：结论写“共识没有可分辨改善”。
- 交叉验证 80% 覆盖率 CI 排除 0.80：证伪“经验 80% 半径已校准”。
- 24/48/72 h 任一时效少于 10 个合格台风或 50 个配对案例：当前扩展样本不足以达到
  原预注册毕业门槛。
- `n_eff < 1.25`：证伪“两个模式等于两个独立意见”的解释。
- 任何资格筛选需要查看误差后人工决定：证伪 round v2 的样本设计。

## 三把刀

1. 状态向量：每个有效时刻的 `X=(latitude, longitude)`；`DYC2` 是两个位置的固定球面函数。
2. 参数与观测：拟合参数 0；两个相关模式位置输入，一个事后 best-track 验证通道；
   `n_eff` 量化输入相关性。
3. 证伪数据：同风暴、同循环、同有效时刻的 IBTrACS `USA_LAT/USA_LON`，以 WGS84
   测地距离和预先冻结判据评分。

## 引用

- [CITED] [UCAR Tropical Cyclone Guidance Project repository](https://hurricanes.ral.ucar.edu/repository/)
- [CITED] [UCAR early/late guidance and interpolated TECH convention](https://hurricanes.ral.ucar.edu/guide/)
- [CITED] [NHC model-aid definitions](https://www.nhc.noaa.gov/verification/verify6.shtml)
- [CITED] [NOAA/NCEI IBTrACS v04r01](https://www.ncei.noaa.gov/products/international-best-track-archive)

