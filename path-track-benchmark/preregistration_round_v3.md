# A 支线路径对比：本地共识与 UKMET 独立核心复验

冻结日期：2026-07-15  
状态：`prospective-independent-core-replication-before-error-read`  
资格：`learning-reproduction`、`unvalidated`

## 已知信息边界

- [MEASURED] round v2 已经公开 `CMC`、`NGX` 及其本地 0.5/0.5 球面共识的误差。
- [MEASURED] 本轮冻结前只审计了源代码、原始 TECH 名称和 TECH 覆盖；尚未把 `UKM`
  位置与 best track 配对，尚未计算 `UKM` 或本地共识在新共同样本上的任何误差、相关或
  `n_eff`。
- [MEASURED] 覆盖预筛显示：round v2 的 26 个合格台风中有 17 个至少存在一个
  `CMC/NGX/UKM` 同循环 `tau=72 h` 记录。2023 年合格台风的 `UKM` 覆盖为 0；该年代
  缺测将在报告中单列，分析范围相应收缩到有覆盖风暴。

## 来源审计与命名冻结

- [MEASURED] `DYC2` 未从任何 a-deck TECH 读取。它由
  `strict_pair()` 对 `CMC` 与 `NGX` 的经纬度执行固定 0.5/0.5 单位球向量平均后写入。
- [MEASURED] 本地 27 份 a-deck 的 TECH 扫描中 `DYC2` 出现 0 次。
- [CITED] 可查的 ATCF objective-aid 示例表与 UCAR late-cycle TECH 清单均未定义
  `DYC2`。因此 round v3 将它重命名为 `LOCAL_EQ2_CMC_NGX`；`DYC2` 只保留为 round v1/v2
  的历史输出别名。
- [CITED] UCAR 将 `UKM` 定义为 UK Met Office model using the development tracker，
  每日 00/12 UTC 运行，tracker 输出未做主观质控。
- [CITED] Met Office 将该模式本体定义为 Unified Model，其动力核心求解可压缩、非静力
  运动方程，采用半拉格朗日平流和半隐式时间步进。这里的“独立”专指相对于 CMC/GEM
  与 Navy NAVGEM/NOGAPS 的模式本体来源及动力核心；共同观测、资料同化输入和后处理
  仍会制造相关误差。

## 数据与样本冻结

1. [MEASURED-DESIGN] 复用 round v2 已冻结的 2022--2024 WP 强台风资格名单和文件哈希。
2. [MEASURED-DESIGN] 模式 TECH 固定为原始 late-cycle `CMC`、`NGX`、`UKM`；不读取
   末尾 `I` 的提前对齐产品，也不做时间插值。
3. [MEASURED-DESIGN] 每一案例必须同时存在同一风暴、同一起报循环、同一提前量的三家
   位置，并且 IBTrACS `USA_LAT/USA_LON` 在完全相同有效时刻存在。
4. [MEASURED-DESIGN] 提前量固定为 24/48/72/96/120 h。所有模式和本地共识按严格
   三方交集评分，保证误差与相关使用同一样本。
5. [MEASURED-DESIGN] 17 个覆盖合格风暴冻结为：`WP012024`、`WP022022`、
   `WP052024`、`WP082024`、`WP112022`、`WP112024`、`WP122022`、`WP122024`、
   `WP142022`、`WP142024`、`WP162022`、`WP182022`、`WP232022`、`WP232024`、
   `WP242024`、`WP252024`、`WP272024`。资格只使用 TECH 字段存在性，未读取误差。

## 计算步骤

1. 将 `CMC` 与 `NGX` 经纬度转为单位球向量，固定等权平均并归一化，生成
   `LOCAL_EQ2_CMC_NGX`；拟合参数为 0。
2. 以 WGS84 测地距离计算 `CMC`、`NGX`、`UKM`、`LOCAL_EQ2_CMC_NGX` 相对
   IBTrACS USA best track 的径向路径误差。
3. 每个提前量报告记录数、台风数、均值、中位数、P80 和按台风 block bootstrap
   2,000 次的 95% CI；随机种子固定为 `20260715`。
4. 在严格三方交集上计算两组相关：
   `CMC vs NGX` 与 `LOCAL_EQ2_CMC_NGX vs UKM`。主结果先分别移除各误差流在每个
   提前量的平均值，再算 Pearson `rho`。同时报告原始 Pearson 与 lead-centered
   Spearman 敏感性。
5. 在交换相关假设下计算

   \[
   n_{eff}=\frac{2}{1+\rho}.
   \]

   `n_eff` 衡量两条误差流的一致性；它不衡量准确性，也不证明完全动力独立。
6. 在每个台风 bootstrap 重采样中同步计算两组 `n_eff`，报告
   `Delta n_eff = n_eff(LOCAL_EQ2,UKM)-n_eff(CMC,NGX)` 的 95% CI。该同步差值控制
   UKM 覆盖造成的样本变化。

## 预注册判据

- [ASSUMED] “接近两个有效意见”定义为主 `n_eff` 点估计位于 `[1.80, 2.20]`，且
  95% CI 下界至少为 `1.60`。
- `Delta n_eff` 的 95% CI 全部高于 0：支持“换入独立 UKMET 核心可增加有效意见数”。
- `Delta n_eff` 的 95% CI 包含 0：当前样本无法分辨独立核心带来的增量。
- `Delta n_eff` 的 95% CI 全部低于 0：证伪该样本中的增量命题。
- `LOCAL_EQ2_CMC_NGX` 与 `UKM` 的主 `n_eff < 1.25`：两条误差流在本样本中只提供
  约一个有效意见。
- 任一主时效少于 10 个台风或 50 个严格配对案例：该时效标记样本不足；仍透明报告，
  不用于“接近 2”的总体宣称。
- 任何看过误差后进行的 TECH、风暴、时效或相关方法调整，必须进入偏离清单。

## 三把刀

1. 状态向量：每个有效时刻 `X=(latitude, longitude)`；本地共识是两个位置的固定函数。
2. 参数与观测：拟合参数 0；三个业务模式位置输入，一个事后 best-track 验证通道；
   本轮只比较误差流的相关性。
3. 证伪数据：同风暴、同循环、同有效时刻的 IBTrACS `USA_LAT/USA_LON`，以 WGS84
   配对误差、台风聚类区间和冻结判据评分。

## 引用

- [CITED] [UCAR late-cycle TECH definitions](https://verif.rap.ucar.edu/jntweb/hurricanes-beta/guide/late/)
- [CITED] [ATCF System Administrator Guide, objective-aid table](https://science.nrlmry.navy.mil/atcf/docs/html/ATCF_SAG_Sec3.html)
- [CITED] [Met Office Unified Model](https://www.metoffice.gov.uk/research/approach/modelling-systems/unified-model)
- [CITED] [NOAA/NCEI IBTrACS v04r01](https://www.ncei.noaa.gov/products/international-best-track-archive)

