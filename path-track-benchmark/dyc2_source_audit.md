# DYC2 来源审计

状态：`source-audit-complete`  
审计日期：2026-07-15

## 已经能用的结论

`DYC2` 是本项目生成的本地诊断量，正式名称现定为
`LOCAL_EQ2_CMC_NGX`。它没有官方 ATCF TECH 身份。

证据链：

1. [MEASURED] `src/path_benchmark/core.py` 的 `strict_pair()` 只接收 `CMC` 与 `NGX`，
   随后以权重 `[0.5, 0.5]` 调用 `spherical_consensus()`，结果写入 `dyc2_*` 字段。
2. [MEASURED] `preregistration.md` 原文写明“构造本项目的两模式等权球面共识
   `DYC2`”。
3. [MEASURED] 本地 27 份 2022--2024 WP a-deck 的 TECH 字段扫描中，`DYC2` 计数为 0。
4. [CITED] UCAR late-cycle TECH 清单列出 `CMC`、`NGX` 系列、`UKM` 等业务 aid，未列
   `DYC2`；ATCF objective-aid 示例表同样未定义该名字。目录缺席只作为辅助证据，
   本地生成代码构成决定性证据。

因此，round v2 的“DYC2 在 5/5 时效更优”应读作：本地 CMC/NGX 固定等权球面共识在
该严格配对样本中取得较小径向误差。它不代表某个中心发布的业务模式，也不代表某个
官方共识产品。

## UKMET 独立核心复验设计

- [CITED] UCAR 将 `UKM` 定义为 UK Met Office model using the development tracker，
  每日 00/12 UTC 运行，tracker 输出无主观质控。
- [CITED] Met Office 官方文档确认其 Unified Model 采用自身动力核心。
- [MEASURED] 本地归档提供原始 `UKM` TECH；round v2 的 26 个合格台风中，17 个在
  `tau=72 h` 至少有一个 `CMC/NGX/UKM` 同循环记录。2023 年该 TECH 在合格样本中
  没有覆盖。

round v3 将在严格三方同样本上比较 `LOCAL_EQ2_CMC_NGX` 与 `UKM`，并同步重算
`CMC vs NGX`。这样可把独立核心差异与 UKM 覆盖造成的样本变化分开。

## 缺口与边界

- `UKM` 的模式本体来源独立；业务资料同化、共同观测和 tracker 处理仍可相关。
- 2023 年整年缺测使 round v3 主要反映 2022 与 2024 样本。
- `n_eff` 只衡量误差一致性；动力独立性需要模式架构文档支撑，准确性需要 best-track
  误差另行评分。

## 引用

- [UCAR late-cycle TECH definitions](https://verif.rap.ucar.edu/jntweb/hurricanes-beta/guide/late/)
- [ATCF System Administrator Guide](https://science.nrlmry.navy.mil/atcf/docs/html/ATCF_SAG_Sec3.html)
- [Met Office Unified Model](https://www.metoffice.gov.uk/research/approach/modelling-systems/unified-model)

