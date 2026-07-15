# 台风多状态马尔可夫模型：文献与数据审计

研究日期：2026-07-12。范围：强度、中心气压、双眼墙结构、海气耦合和可证伪性。

## 1. 文献结论

| 来源 | 可直接采用的部分 | 对本模型的约束 |
|---|---|---|
| Jing & Lin (2019), *Journal of Climate*, [DOI](https://doi.org/10.1175/JCLI-D-19-0027.1), [NOAA 全文](https://repository.library.noaa.gov/view/noaa/59904/noaa_59904_DS1.pdf) | 三个隐状态、环境依赖的多项逻辑转移、6 小时强度变化；输入含 MPI、风切、湿度和海洋反馈 | 隐状态必须有时间持续性；转移核只读取当前状态与当前环境；RI 概率必须单独校准 |
| Emanuel & Zhang (2017), *JAS*, [DOI](https://doi.org/10.1175/JAS-D-17-0008.1) | `V` 与内核水汽 `m` 的两条耦合 ODE | 连续状态至少包含 `V+m`；单变量 `V` 会丢失内核水汽记忆 |
| Lin et al. (2023), *JAMES*, [DOI](https://doi.org/10.1029/2023MS003686), [开放代码](https://github.com/linjonathan/tropical_cyclone_risk) | FAST 方程、海洋反馈公式、WP 边界层深度 1800 m、`Ck=1.2e-3`、`epsilon=0.33`、`kappa=0.1` | 这些量固定为文献常量；PI、风切、水汽亏缺和海洋剖面作为外部强迫 |
| Sparks & Toumi (2022), *GRL*, [DOI](https://doi.org/10.1029/2022GL098926), [全文](https://spiral.imperial.ac.uk/server/api/core/bitstreams/93b64752-4993-4018-a24c-ef40f8f35c98/content) | `dPc/dt=-2 Pc chi/RMW`；6 小时内可用指数解；典型 `chi=+0.29/-0.24 km day^-1` | 中心气压和 RMW 进入动态状态；较小 RMW 对应更快气压变化；`chi` 由离散机制状态给出 |
| Wu & Ruan (2021), *JAS*, [DOI](https://doi.org/10.1175/JAS-D-21-0129.1) | `M=RV+0.5 fR^2` 及其沿 RMW 的时间导数 | v0 采用 `dM/dt=0` 的零阶闭合，得到 `dR/dt=-R(dV/dt)/(V+fR)`；RMW 观测直接检验该闭合 |
| NOAA NCEI IBTrACS v04r01, [主页](https://www.ncei.noaa.gov/products/international-best-track-archive), [字段文档](https://www.ncei.noaa.gov/sites/default/files/2025-09/IBTrACS_v04r01_column_documentation.pdf) | 各机构风速、中心气压、USA/JTWC RMW、平均风速时段元数据 | CMA 2 分钟、JMA/HKO 10 分钟、USA/JTWC 1 分钟必须分开处理；训练采用同一机构口径 |
| ERA5 pressure levels, [Copernicus](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels) | 1940 年至今、37 层、逐小时温湿风廓线 | PI 由完整廓线计算；250/850 hPa 风切和中层水汽亏缺来自同一有效时刻 |
| tcpyPI, [代码与验证资料](https://github.com/dgilford/tcpyPI) | Bister-Emanuel 2002 PI 的公开 Python 实现 | PI 数据管线采用完整温度/比湿/气压廓线；2 m 温湿度简式退出模型 |
| Copernicus Marine GLORYS12, [历史再分析](https://data.marine.copernicus.eu/product/GLOBAL_MULTIYEAR_PHY_001_030/description), [实时分析预报](https://data.marine.copernicus.eu/product/GLOBAL_ANALYSISFORECAST_PHY_001_024/description) | 1/12 度、50 层温盐剖面和混合层厚度；实时产品每日更新并提供 10 天海洋预报 | 历史回报用 GLORYS12，实时强迫用同系 GLO12；`Gamma` 从 MLD 下方 100 m 温度梯度计算 |
| Gee & Ravela (2026), [arXiv:2601.08116](https://arxiv.org/abs/2601.08116) | 从 IBTrACS 与 ERA5 学得紧凑强度 SDE，并复现部分非线性结构 | 作为数据驱动标量基线参加回报检验；本模型继续用 `V、Pc、RMW` 三通道检验结构 |
| Kuo et al. (2009), [DOI](https://doi.org/10.1175/2009MWR2850.1)；Yang et al. (2013), [DOI](https://doi.org/10.1175/MWR-D-12-00251.1) | 被动微波识别内眼墙、moat、外眼墙及置换/维持分类 | 单一 RMW 无法表达同心双环；亮温阈值、环完整度和覆盖必须进入标签误差 |
| Kossin et al. (2023), [DOI](https://doi.org/10.1175/WAF-D-22-0178.1), [NOAA 全文](https://repository.library.noaa.gov/view/noaa/53696/noaa_53696_DS1.pdf) | ARCHER 从 0-200 km 每 6 km 计算 ring score；M-PERC 使用其 24 小时演变诊断 ERC onset | 微波通道进入径向结构观测算子；ERC 检验使用 Brier score 和可靠性，而非类别 accuracy |
| TC PRIMED, [产品](https://rammb-data.cira.colostate.edu/tcprimed/products.html), [文档](https://rammb-data.cira.colostate.edu/tcprimed/TCPRIMED_v01r01_documentation.pdf) | 多传感器 Level-1C 亮温、相对中心的 `x/y`、覆盖率和来源元数据 | 历史训练与密封集的主微波源；final 最佳路径中心仅用于回顾性结构提取 |
| ESA CYMS / CyclObs, [数据访问](https://www.esa-cyms.org/data-access/), [PUM](https://www.esa-cyms.org/wp-content/uploads/2020/11/PUM_L2_CYMSproducts_20201119.pdf) | 瞬时 3 km SAR 海面风场、台风极坐标产品、中心质量和 RMW | 直接验证 `(V1,R1,V2,R2)`；巴威 16 景中 4 景通过探索性双峰质控 |

Jing & Lin 的 MeHiM 在 332 个训练风暴、9809 个海上时次上拟合，并报告极端 RI 状态仍有低估。它为状态转移形式提供先例，也为本模型设定了明确基线：RI 的 Brier 分数和可靠性曲线必须独立检验。

Sparks & Toumi 说明 `chi` 难以直接观测。v0 将 `chi` 限定为三个离散值，并使用 `Pc` 与 `RMW` 两个观测序列共同证伪；逐时自由系数数目固定为 0。

结构复核显示，Sparks--Toumi 离散 `chi` 只改变 `Pc`，FAST 风速方程保持独立。因此三个隐状态对 `V` 的条件分布没有影响，无法复现 MeHiM 中“每个隐状态对应一套强度变化分布”的核心结构。

## 2. 当前数据盘点

2026-07-11 下载 NCEI 的 `ibtracs.WP.list.v04r01.csv`，服务端版本时间为 2026-07-09。筛选 `2001-2024`、`TRACK_TYPE=main` 后得到：

- 719 个西北太平洋风暴，47,699 条主轨迹记录。
- USA/JTWC `WIND+PRES+RMW` 同时为正值：33,308 条，覆盖 679 个风暴。
- CMA `WIND+PRES` 同时为正值：38,415 条。
- JMA `WIND+PRES` 同时为正值：23,439 条。

这些逐时次记录具有强序列相关性。样本划分以风暴为单位，参数审计使用灵敏度矩阵秩和有效样本量，原始行数只用于数据可用性盘点。

RMW 在西北太平洋主要来自 JTWC 卫星与业务估计。Sparks & Toumi 对北大西洋飞机观测区给出更高置信度，并指出其他海盆可能出现定量差异。因此 RMW 作为评分字段，同时保留观测质量标签、相关结构和分层误差报告。

完整样本的 `corr(V,Pc)=-0.9817`；三通道相关矩阵的参与率有效维数为 `1.4641`，熵有效秩为 `1.6821`。RMW 中 `95.80%` 为 5 海里整倍数，连续六小时时次保持不变的比例为 `65.82%`。参与率用于诊断观测冗余；正式参数可识别性由观测误差协方差白化后的灵敏度/Fisher 信息与剖面似然判定。

ERC 数据源、字段和巴威 SAR 审计见 [erc-microwave-data-source-audit.md](erc-microwave-data-source-audit.md)。

## 3. 设计结论

v0.1 连续状态取 `X=(V,m,Pc,RMW)`，离散状态取 `Z in {减弱,准稳态,增强}`。三个评分字段为 `V、Pc、RMW`；`m` 是由起报前强度趋势初始化的潜变量。

全局可调数字只有两个：状态持续性 `persistence_logit` 和状态宽度 `regime_width`。其余数字分为文献固定常量、单位定义、观测初值和带来源的环境强迫。参数校准期截止于当前风暴形成以前。

结构检验已经淘汰 v0.1。下一版须让离散机制直接改变风速变化条件分布，并以微波环形对流和 SAR 双风峰约束 `(V1,R1,V2,R2)`。正式可识别性审计需要登记观测误差协方差、白化灵敏度/Fisher 信息和剖面似然；实时概率预报资格继续由历史回报检验和预注册门槛授予。
