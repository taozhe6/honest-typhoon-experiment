# B 支线：外部登陆实测风审计

冻结协议：[`landfall_truth_protocol.md`](landfall_truth_protocol.md)
状态：`external-observation-audit-complete`

## 这轮做成了什么

1. **[验证过] 108 个冻结登陆事件已完成四个地面观测档案的逐事件检索；11 个台湾案例另完成 CWA TDB 结构化支持档案全量筛查，全部来源访问门槛已逐项裁决。** A 级可评分覆盖为
   `4/108`（3.7%）；A+B 原始实测覆盖为
   `108/108`（100.0%）。IBTrACS 包内真值仍为 0，
   包外地面观测层已经覆盖 108 个事件。
2. **[验证过] `landfall_truth.csv` 保存 16289 条事件关联的站点极值。**
   每条记录均保留风种、平均窗数值或未知标记、质量码、时空距离、来源 URL、原始文件哈希与逐条 A/B 裁决。
3. **[验证过] A 级评分闸门得到数据裁决。** 当前严格判据下 A 级事件数为
   4；五家对独立中心 Vmax 的 bias、MAE、RMSE 与真误差相关状态见下表。
4. **[验证过] 注册、申请和付费路径已按停止规则封口。** CMA 国家站/雷达、KMA、
   PAGASA、TMD 与越南国家档案均保留访问门槛证据，未绕过访问控制。

### CWA TDB 支持证据

- [验证过] CWA 年度警报清单自动匹配 `11/11` 个冻结台湾案例，台风 ID 由公开
  清单发现，脚本未写死事件编号。
- [验证过] `11/11` 个案例的完整雷达产品元数据已枚举，每案缓存最接近冻结
  登陆时刻及署属站峰值时刻的合成反射率图；反射率只承担结构定位支持，直接风速算子仍归 C 级。
- [验证过] `11/11` 个案例取得数据库提供的官方事件 PDF；逐站小时风接口形成
  `4086` 条站点极值交叉核对，其中 `4049` 条与 CODiS 保留站点相接。
- [验证过] `landfall_truth_support_evidence.csv` 与 `cwa_tdb_station_crosscheck.csv` 保存上述
  枚举结果。两表复核同一批 CWA 仪器，不重复计入 `landfall_truth.csv` 的事件覆盖。
- [推测] 单人统一坐标叠加审查把 4 个低海拔署属站判为眼墙共址 A 级，7 个案例保留 B 级；
  逐案理由、雷达哈希与站点暴露位于 `cwa_tdb_eyewall_review.csv`。该人工结构判读是本轮
  A 级结果的主要不确定性。

## A/B 观测采集

|来源|记录数|覆盖事件|A级记录|B级记录|
|---|---:|---:|---:|---:|
|CWA_CODIS|8813|19|4|8809|
|HKO_TC_IMPACT|2018|34|0|2018|
|JMA_AMEDAS|3393|29|0|3393|
|NOAA_ISD|2065|108|0|2065|

- **A 级**要求原生可比持续风、质量通过、登陆 +/-6 h、足够采样密度，以及独立证据证明
  测点处于眼墙/最大风区。
- **B 级**仍是仪器实测；风窗、时间采样或最大风区代表性至少有一项未闭合。
- **C 级**反演、机构分析和无法核实数字位于
  `outputs/b_branch/landfall_truth_exclusions.csv`。

### 登陆地区覆盖

登陆地区由 Natural Earth admin-0 最近多边形赋值，台湾四个 A3 字段已核验为 `TWN`。

|代码|地区|冻结事件|A级|A+B级|
|---|---|---:|---:|---:|
|CHN|China|30|0|30|
|JPN|Japan|21|0|21|
|KOR|South Korea|4|0|4|
|PHL|Philippines|31|0|31|
|PRK|North Korea|1|0|1|
|THA|Thailand|1|0|1|
|TWN|Taiwan|11|4|11|
|VNM|Vietnam|9|0|9|

## 五家对 A 级真值

误差定义为 `agency_10min - external_truth_10min`，单位 m/s。数值格式为
`点估计 [95% CI 下限, 上限]`；区间按 SID bootstrap 2,000 次，样本为 0 时保持 NA。

|机构|A 级事件|bias|MAE|RMSE|状态|
|---|---:|---:|---:|---:|---|
|JTWC|4|12.6 [9.1, 18.5]|12.6 [9.1, 18.5]|13.6 [9.1, 19.1]|measured_against_external_grade_a_truth|
|JMA|4|6.0 [3.7, 9.5]|6.0 [3.7, 9.5]|6.8 [3.7, 10.1]|measured_against_external_grade_a_truth|
|CMA|4|7.9 [3.2, 13.9]|7.9 [3.2, 13.9]|9.5 [3.8, 14.6]|measured_against_external_grade_a_truth|
|HKO|4|12.4 [9.3, 17.6]|12.4 [9.3, 17.6]|13.2 [9.3, 18.2]|measured_against_external_grade_a_truth|
|KMA|4|6.3 [2.7, 9.9]|6.3 [2.7, 9.9]|7.4 [2.7, 10.1]|measured_against_external_grade_a_truth|

[验证过] 这个表衡量机构分析与满足预注册 A 级条件的外部观测之差。CMA/CWA/HKO/JMA
测站资料可能进入相应机构业务信息集，原始测量保持独立，业务信息集并非完全隔离；CMA
在中国案例还具有主场信息优势。

[推测] A 级仍是固定低海拔站点的 10 分钟最大平均风，机构量是中心附近空间最大值；眼墙
共址缩小了空间算子差异，仍无法把两者变成完全相同的量。当前误差区间只有 4 个事件，
用于测量这 4 案的差值，不能外推为五家长期业务准确率。

### A 级真误差相关

单元格为 `rho [95% CI]`。区间按 SID block bootstrap 2,000 次；
每个 A 级事件只出现一行，故这里的 block 单位与事件单位一致。最少有效相关重采样数为
1,964；重复抽中单一事件或零方差的重采样按未定义剔除。

|机构|JTWC|JMA|CMA|HKO|KMA|
|---|---:|---:|---:|---:|---:|
|JTWC|1.00 [1.00, 1.00]|-0.25 [-1.00, 1.00]|0.90 [-1.00, 1.00]|1.00 [0.66, 1.00]|0.82 [-1.00, 1.00]|
|JMA|-0.25 [-1.00, 1.00]|1.00 [1.00, 1.00]|0.05 [-1.00, 1.00]|-0.22 [-1.00, 1.00]|-0.45 [-1.00, 1.00]|
|CMA|0.90 [-1.00, 1.00]|0.05 [-1.00, 1.00]|1.00 [1.00, 1.00]|0.87 [-1.00, 1.00]|0.84 [-1.00, 1.00]|
|HKO|1.00 [0.66, 1.00]|-0.22 [-1.00, 1.00]|0.87 [-1.00, 1.00]|1.00 [1.00, 1.00]|0.76 [-1.00, 1.00]|
|KMA|0.82 [-1.00, 1.00]|-0.45 [-1.00, 1.00]|0.84 [-1.00, 1.00]|0.76 [-1.00, 1.00]|1.00 [1.00, 1.00]|

[验证过][MEASURED] 点值和区间完整保存于
`independent_truth_error_correlation.csv` 与
`independent_truth_error_correlation_intervals.csv`。4 个 A 级事件全部位于台湾，区间主要反映
这 4 案的有限重采样；矩阵可描述该子集中的误差同向性，长期机构误差相关仍需更多 A 级事件。

## 来源全面调查

|来源|可得性|依据|访问方式|格式|
|---|---|---|---|---|
|NOAA/NCEI Global Hourly (ISD/GTS)|available|[验证过] direct anonymous station-year CSV downloads succeeded|anonymous HTTPS WAF|CSV; WND field with report type and QC|
|Taiwan CWA CODiS station archive|available|[验证过] anonymous station-list and multi-station historical POST requests succeeded|anonymous HTTPS web API used by CODiS|JSON; hourly bins with TenMinutelyMaximum/Mean/PeakGust and flags|
|Taiwan CWA Typhoon Database radar and station support archive|partially_available|[验证过] anonymous warning registry, station time series, 6-minute reflectivity images and official PDFs were enumerated for every frozen Taiwan case|anonymous HTTPS endpoints used by the public database|JSON metadata and station rows; JPEG/GIF/PDF products|
|HKO Tropical Cyclone Impact Dataset and annual publications|partially_available|[验证过] anonymous official XLSX download contains station passage maxima through 2025|anonymous HTTPS|XLSX; gust and maximum hourly mean wind by station|
|HKO regional 10-minute wind open data|partially_available|[据文档] public feed is explicitly the latest value; historical 2015-2024 snapshots are not supplied|anonymous latest-only CSV|CSV|
|JMA historical AMeDAS/surface observation download|available|[验证过] anonymous multi-station historical CSV POST requests succeeded|anonymous official download service with session cookie|Shift-JIS CSV with values, QC and homogeneity number|
|CMA national ground observation archive|unavailable|[验证过] dataset page requires personal/unit real-name registration|real-name registered users|hourly/three-hourly/daily datasets|
|China coastal Doppler radar archive/products|unavailable|[验证过] CMA radar product pages require real-name registration; public pages expose derived images|real-name registration; raw archive not anonymously downloadable|radar base/product files or public derived images|
|KMA ASOS historical observations|unavailable|[据文档] official OpenAPI requires a utilization application/key|registration/application|API|
|PAGASA historical station observations|unavailable|[据文档] official request path requires forms/supporting documents and can charge fees|application and fee schedule|requested tables/files|
|Thai Meteorological Department historical observations|unavailable|[验证过] official service lists login/request and per-station hourly fees|registration, application and payment|download after approval/payment|
|Vietnam national hydrometeorological database|unavailable|[据文档] official procedure requires a written request and payment where applicable|formal request and possible fee|provided on request|
|Official post-storm reviews and peer-reviewed papers|partially_available|[验证过] case-specific public station/gust/radar values exist; coverage and observation operators vary|anonymous HTML/PDF where published|HTML/PDF|

### 独立性和平均窗

- [据文档] ISD `WND` 的 `N` 类型缺少统一编码平均窗；`H/R/T` 分别标识
  5/60/180 分钟。ISD 记录全部按 B 级处理。
- [据文档] JMA AMeDAS 风速是观测时刻前 10 分钟平均；本次匿名下载只取质量码 8。
  逐小时抽样可能漏掉时内峰值，因此保持 B 级。
- [验证过] CWA 署属站 JSON 提供 `TenMinutelyMaximum`；自动站 `Mean/WS` 的平均窗没有
  在公开字段中闭合，自动站继续保持 B 级。
- [推测] 署属站候选用同一时刻附近的官方 6 分钟合成反射率审查眼墙共址；4 个低海拔、
  QC 通过的候选升为 A，兰屿 324 m、玉山 3844.8 m 及外围站点保持 B。
- [验证过] HKO 影响数据集提供逐站阵风和最大 60 分钟平均风；精确峰值时刻未进入工作簿，
  两种量均保持 B 级。
- [验证过] CWA 检索纳入与 +/-12 h 边界相交的完整小时箱；箱结束时刻可落到 +/-13 h，
  `observation_period_start/end` 保存实际区间，敏感性按记录时刻严格裁剪。
- [验证过] HKO 工作簿是整个警告影响期极值，记录可远离冻结首次登陆点；距离和影响期边界
  均逐条保存，全部保持 B 级。

## 覆盖敏感性与缺测

[验证过] 检索主窗为 250 km、+/-12 h。`landfall_truth_source_audit.json` 保存
50/100/150/250 km 与 +/-3/6/12 h 的事件覆盖敏感性。HKO passage-wide 记录缺少精确峰值
时刻，因此不进入时窗敏感性计数。

[验证过] 每个事件的来源级记录数和最终状态位于
`landfall_truth_event_coverage.csv`；108 个事件乘全部 13 个来源的逐格裁决位于
`landfall_truth_source_event_status.csv`。缺测并非随机：国家档案访问政策、站网密度、风窗编码、
台风路径与岛陆位置共同决定覆盖。ISD 的全球 GTS 汇集显著提高 B 级覆盖，同时保持来源国
原档案门槛的独立披露。

## 真值边界

[验证过] 西北太平洋 1987 年后缺少常态飞机侦察；沿岸风速仪测得的是固定点、固定暴露的风，
机构 Vmax 描述中心附近空间最大持续风。原生平均窗一致只闭合时间算子，最大风区证据还需
雷达眼墙定位或密集站网的可审计空间包络。

当前数据支持“公开地面观测对这些登陆事件覆盖到什么程度”。A级闸门决定五家绝对误差
是否可识别；B 级记录不进入评分。

## 预注册偏离

- [验证过] 无 A/B/C 判据偏离。
- [验证过] CODiS 站点接口采用每批 150 站，CWA TDB 自动站接口采用每批 100 站；两项变化
  只减少网络请求数，不改变站点集合、时间窗或分级规则。
- [验证过] HKO 合并工作簿提供 60 分钟平均风，原先拟议的逐年网页表改由同一官方汇编
  全量读取；平均窗语义保持 60 分钟并归 B 级。
- [验证过] 预注册要求按国家/地区报告覆盖，执行阶段增加 Natural Earth admin-0 最近多边形
  赋值；该字段只分组描述覆盖，不参与 A/B 分级和机构评分。
- [验证过] CWA TDB 的年度警报清单提供可枚举事件 PDF，执行阶段据此补齐全部 11 个台湾
  案例；其他国家官方复盘与论文仍按逐案、非完整机器索引来源登记。
- [验证过] 预注册已指定雷达眼墙定位作为 A 级门槛；执行阶段增加一份 11 案单人雷达
  叠加标注表。判据未改动，人工判读统一标为 `[推测]` 并保留原图哈希。

## 三把刀

1. **状态向量。** 本任务没有预测状态；测量记录为
   `(station, time/window, location, wind, averaging window, QC, grade)`。
2. **参数与独立观测。** 拟合参数为 0；A 级独立事件为 4，B 级覆盖事件为
   108。访问半径和时窗属于预注册检索常量，并已做离散敏感性。
3. **证伪数据。** 带原始哈希的匿名站点档案可复查每条测量；独立雷达眼墙定位、明确
   10 分钟持续风和完整峰值时序可把具体 B 记录升级为 A，也可证伪当前的保守分级。

## 复现

```bash
cd "/Users/taozhe/Documents/New project/typhoon/ibtracs-agency-disagreement"
.venv/bin/python scripts/run_landfall_truth.py --check
.venv/bin/python scripts/run_landfall_truth.py --offline --check
```

原始下载位于被 Git 忽略的 `data/raw/landfall_truth/`。公开产物保存来源 URL、SHA-256、
逐条裁决和源级访问证据。
