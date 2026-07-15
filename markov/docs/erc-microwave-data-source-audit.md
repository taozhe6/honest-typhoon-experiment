# 眼墙置换观测与数据源审计

审计日期：2026-07-12。状态：数据源已确认，v0.2 方程设计继续等待观测误差与密封标签集。

## 1. 数据源决策

| 任务 | 主数据源 | 用途 | 时效与边界 |
|---|---|---|---|
| 历史被动微波结构 | [NOAA/CSU TC PRIMED](https://rammb-data.cira.colostate.edu/tcprimed/products.html) | 85-92 GHz 亮温径向结构、传感器间校准、2022-2024 密封回报 | final 文件使用事后最佳路径中心，仅用于回顾性结构提取 |
| 准实时被动微波 | [NASA PPS Level-1C NRT](https://gpm.nasa.gov/node/3313) | 当前风暴的多传感器亮温扫描 | 最近两周，PPS 注册访问，业务管线记录首次可见时刻 |
| 环形结构算法基准 | [CIMSS ARCHER / M-PERC](https://tropic.ssec.wisc.edu/real-time/archerOnline/web/index_erc.shtml) | 中心定位、环形评分、ERC 概率与算法复核 | 极轨过境时间不规则，M-PERC 需要此前 24 小时结构历史 |
| 海面风环直接验证 | [ESA CYMS / CyclObs](https://www.esa-cyms.org/data-access/) | SAR 10 m 风场、双风速峰、RMW 和风圈 | 过境稀疏，单景为瞬时 3 km 有效分辨率观测 |
| 长年代眼区补充 | [NOAA HURSAT](https://www.ncei.noaa.gov/products/hurricane-satellite-data) | 眼半径、眼墙半径、眼完整度的历史先验 | 红外结构，1978-2015，适合作为补充通道 |

机器可读注册表位于 `config/erc_observation_sources.json`，阈值与语义位于 `config/eyewall_structure_observation_contract.json`。

## 2. 论文给出的观测对象

[Kuo et al. (2009)](https://doi.org/10.1175/2009MWR2850.1) 使用西北太平洋 1997-2006 年被动微波影像识别同心眼墙，并指出亮温阈值、外环完整度与缺测覆盖会显著改变案例数。[Yang et al. (2013)](https://doi.org/10.1175/MWR-D-12-00251.1) 将结构量扩展为内眼墙半径、moat 宽度和外眼墙宽度，并区分置换、长期维持与其他演变。

[Kossin et al. (2023) M-PERC](https://doi.org/10.1175/WAF-D-22-0178.1) 给出当前最直接的业务化定义：ARCHER 在 85-92 GHz 图像上从中心到 200 km 每 6 km 计算 ring score，搜索外侧次峰及其收缩；训练标签要求外对流环与内眼墙清楚分离且至少完成 75% 圆周。训练集含 47 个大西洋风暴、1787 条径向廓线和 84 次 ERC onset，独立期使用 Brier skill score 验证。

ring score 描述圆形对流和降水冰散射结构。SAR 的 `wind_speed(r, theta)` 描述海面 10 m 风结构。两者通过不同观测算子约束同一个潜在双风环状态。

## 3. TC PRIMED 实物检查

本次从 NOAA 公共 S3 下载并打开真实 AMSR2 文件：

`TCPRIMED_v01r01-final_WP052023_AMSR2_GCOMW1_059527_20230727053135.nc`

核实字段如下：

- `passive_microwave/S5/TB_A89.0H`，单位 K，形状 `214 x 486`。
- 89 GHz IFOV 为沿轨 5 km、横轨 3 km。
- `x/y` 以最佳路径插值中心为原点，单位 km，采用 UTM 距离。
- `coverage_fraction` 描述 S1 swath 在中心 750 km 圆内的覆盖率。
- `overpass_storm_metadata/intensity` 明确为 10 m、1 分钟平均最大持续风，单位 kt。

密封期西北太平洋文件清单审计：

| 年份 | 风暴数 | 微波过境文件 | AMSR2 | ATMS | GMI | MHS | SSMIS |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 29 | 2601 | 219 | 648 | 152 | 882 | 700 |
| 2023 | 18 | 2818 | 213 | 952 | 145 | 845 | 663 |
| 2024 | 28 | 3272 | 260 | 1099 | 182 | 1000 | 731 |

这些数字证明原始亮温样本量充足。每景仍需检查内核覆盖、传感器足迹、中心误差和可用亮温比例。[TC PRIMED 文档](https://rammb-data.cira.colostate.edu/tcprimed/TCPRIMED_v01r01_documentation.pdf)明确说明其 final 中心来自事后最佳路径，并主动省略缺少历史季后质控的 RMW 字段。

## 4. 巴威 CyclObs SAR 实证

动态 API 查询 `sid=wp092026` 返回 16 次 SAR 过境。输出来源为：

`https://cyclobs.ifremer.fr/app/api/getData?sid=wp092026`

审计采用 `quality_level >= 3`、中心质量 `< 2`、方位覆盖率 `>= 0.5`。1 km 径向均值经过 9 km 移动平均；双峰候选要求每峰 prominence `>= 0.5 m/s` 且半径间隔 `>= 30 km`。这些峰值阈值属于探索性预注册阈值，密封人工标签集将决定其去留。

| 有效时刻 UTC | 任务 | 内峰 km | 外峰 km | 中心质量 | 判定 |
|---|---|---:|---:|---:|---|
| 2026-07-08 09:19 | RCM-1 | 37 | 141 | 1.3 | 双峰候选 |
| 2026-07-08 21:14 | RCM-1 | 45 | 106 | 1.0 | 双峰候选 |
| 2026-07-09 09:26 | RCM-1 | 43 | 104 | 0.0 | 双峰候选 |
| 2026-07-09 21:27 | Sentinel-1D | 37 | 123 | 0.0 | 双峰候选 |
| 2026-07-11 09:52 | Sentinel-1C | 26 | 102 | 1.8 | 双肩结构，prominence 约 0.4 m/s |

7 月 11 日景的 CyclObs 产品给出 `msw=38.62 m/s`、`rmw=108 km`；轴对称廓线最大值为 `29.93 m/s`，位于 `102 km`。`msw` 是二维场最大值，轴对称最大值是方位平均，两者属于不同统计量。该景表明外侧风环已主导宽广风场，单一 RMW 会抹去残留内峰。

2022-2024 西北太平洋 CyclObs 清单共有 309 景、45 个风暴，其中 217 景覆盖台风眼。SAR 适合构建稀疏高质量验证集，TC PRIMED 提供密集微波结构序列。

可复现脚本：

```bash
cd "/Users/taozhe/Documents/New project"
python3 -m pip install -r typhoon/markov/requirements-research.txt
python3 typhoon/markov/scripts/audit_cyclobs_structure.py \
  --sid wp092026 \
  --start-date 2026-07-01 \
  --stop-date 2026-07-13 \
  --output typhoon/markov/outputs/bavi_2026_cyclobs_structure_audit.json
```

脚本从注册表读取 API，按 SID 和日期动态发现产品，保存 API 响应哈希、每个 NetCDF 哈希、处理版本、质量字段、完整径向风速廓线和候选峰。

密封期库存与当前公开目录由 `scripts/audit_erc_source_availability.py` 复现，带哈希结果位于 `outputs/erc_source_availability_2022_2026.json`。截至本次审计，TC PRIMED 2026 preliminary 西北太平洋目录公开 `01/02`，ARCHER 公开目录列到 `07W`，CyclObs 已公开 `wp092026` 的 16 景风场。三个目录的更新节奏不同，业务管线分别记录其首次可见时刻。

## 5. 下一版观测模型

潜在物理状态至少包含：

\[
X_t=(V_{1,t},R_{1,t},V_{2,t},R_{2,t},m_t,P_{c,t},\ldots).
\]

两个观测通道分别为：

\[
Y^{\mathrm{PMW}}_t(r,\theta,\nu)=h_{\mathrm{hydrometeor}}(X_t,\nu)+\epsilon^{\mathrm{PMW}}_t,
\]

\[
Y^{\mathrm{SAR}}_t(r,\theta)=U_{10}(X_t,r,\theta)+\epsilon^{\mathrm{SAR}}_t.
\]

被动微波约束环形对流几何、moat 和外环收缩；SAR 约束真实风速峰及半径。状态维数和全局自由参数数目属于两个概念。物理闭合、共享层级参数与观测误差模型共同决定自由参数预算。

## 6. 可识别性修正

`n_eff=1.4641` 证明 `V/Pc/RMW` 三个字段高度冗余。参与率描述观测相关矩阵的有效维数，正式参数可识别性由参数尺度化、观测误差协方差白化后的灵敏度矩阵、Fisher 信息条件数和剖面似然共同判定。

代码中的硬门槛已改为：

1. 登记观测误差协方差。
2. 白化且参数尺度化的灵敏度矩阵满列秩。
3. Fisher 信息条件性与剖面似然区间通过预注册标准。
4. 风暴级训练集和密封集完全分离。

参与率保留为冗余诊断。当前正式可识别性状态为 `not_run`，v0.1 继续保持 `research-rejected`。

## 7. v0.2 开工闸门

1. 从 TC PRIMED 提取 85-92 GHz 的 storm-centered ring-score 或等价径向结构。
2. 由两名独立标注者盲标 secondary ring、收缩、inner-wind-maximum loss，报告一致性。
3. 使用 CyclObs SAR、岸基雷达和可得飞机资料估计观测误差与标签误差。
4. 统计 2022-2024 每个预报时次在 `available_at <= issue_time` 下的实际覆盖。
5. 冻结转移方程、自由参数和误差模型，再运行风暴级密封回报检验。

当前证据足以批准数据工程，尚未授予 v0.2 强度预测资格。
