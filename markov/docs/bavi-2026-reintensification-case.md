# 巴威 2026 再增强证伪案例

## 冻结窗口

| 通道 | 初始分析 | 12 h 预报 | 后续分析 | 原生变化 | 预报误差 |
|---|---:|---:|---:|---:|---:|
| NMC，2 分钟 | 40 m/s | 42 m/s | 42 m/s | +2 m/s | 0 m/s |
| JMA，10 分钟 | 85 kt | 85 kt | 80 kt | -5 kt | +5 kt |
| JTWC，1 分钟 | 75 kt | 75 kt | 85 kt | +10 kt | -10 kt |

NMC 与 JMA 窗口从 2026-07-10 09Z 到 21Z。JTWC 第 38 号警报在 09Z 发布，分析时次为 06Z，验证时次为 18Z。每条记录在 `cases/bavi_2026_reintensification.json` 中携带起报、有效时次、平均风窗、风高、原生单位、标准单位、位置、气压和来源 ID。

## 数据来源

- [NMC 历史台风详情](https://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_3257931?callback=typhoon_jsons_view_3257931)
- [GDACS 原始 GTS 报文归档](https://www.gdacs.org/gts.aspx?eventid=1001279&eventtype=TC)
- [UCAR ATCF a-deck 归档](https://hurricanes.ral.ucar.edu/repository/data/adecks_open/awp092026.dat)
- [CMA 2 分钟风定义](https://www.cma.gov.cn/wmhd/gzly/cjwt/202311/t20231127_5912128.html)
- [JMA 10 分钟风定义](https://www.jma.go.jp/jma/jma-eng/jma-center/rsmc-hp-pub-eg/advisory.html)
- [JTWC 1 分钟风定义](https://www.metoc.navy.mil/jtwc/products/best-tracks/tc-bt-report.html)

构建器分别保存整页响应 SHA-256 与入选证据的规范化 SHA-256。GDACS 页面含动态 HTML，复现实验以三份入选原始报文的规范化哈希为准。报文值由结构化解析器提取，案例规格文件只冻结风暴 ID、消息 ID、起报时次和提前量。

## 模型判决

v0.1 的风速方程为 `dV/dt = FAST(V,m,U)`，隐状态 `Z` 只进入气压方程。两组反事实探针分别固定 `X,U` 并轮换三个 `Z`，所得三条 `dV/dt` 完全相同，最大差为 `0.0 m/s/day`。

- regime 风速机制测试：`FAIL`。
- 完整巴威积分：`INELIGIBLE`。
- 缺失资格产物：通过审计的校准、业务时点初值、业务时点环境强迫、机构观测算子。
- 密封回报资格：保留，2022--2024 风暴互斥测试仍为总体结论来源。

运行结果保存在 `outputs/bavi_2026_reintensification_case.json`。
