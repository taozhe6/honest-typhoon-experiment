# 全局固定常量敏感性预注册

预注册日期：2026-07-15。状态：`preregistered-before-perturbation-results`。

## 1. 目的

按项目目标对 FAST 的 `Ck`、西北太平洋边界层深度 `h`、`kappa` 分别运行 ±30% 敏感性，并检验 `Ck/h` 的结构混淆。该审计属于 v0.1 失败基线的合成实验，不产生巴威预报或业务强度结论。

## 2. 预注册前已知信息

- [MEASURED] 现有 `synthetic_smoke.json` 的基准 48 小时轨迹已经查看。
- [CITED] 上游 [Lin et al. 开源实现](https://github.com/linjonathan/tropical_cyclone_risk) 的审计 commit 为 `a540a1ed86b121f7244557edb691b40561b81939`。
- [CITED] 上游代码给出 `Ck=1.2e-3`、WP `h=1800 m`、`epsilon=0.33`、`kappa=0.1`。
- [MEASURED] 本模型的 FAST 前因子写成 `0.5*Ck/h`；联合同比缩放的数值结果仍未查看。

## 3. 冻结实验

机器可读场景和扰动全部写入 `config/global_sensitivity.json`。三个合成场景为：

1. `open_ocean_intensifying`：开放海洋、前期增强、后期趋稳。
2. `hostile_open_ocean`：较低 PI、较强风切和熵亏缺。
3. `landfall_transition`：海洋到全陆地的 48 小时过渡。

每个场景运行两套引擎：

- [ASSUMED] **固定 regime 引擎**：使用配置内 8 个预定 regime，隔离连续 FAST/压力/RMW 方程。
- [ASSUMED] **完整 Markov 引擎**：使用未拟合 demo 参数 `persistence_logit=1.2`、`regime_width=0.6`，每个扰动与基准共享同一随机种子。

## 4. 扰动

- [ASSUMED] `Ck × {0.7,1.3}`，其余常量固定。
- [ASSUMED] `h × {0.7,1.3}`，其余常量固定。
- [ASSUMED] `kappa × {0.7,1.3}`，其余常量固定。
- [ASSUMED] 结构控制：`Ck` 与 `h` 同时乘 `{0.7,1.3}`，保持 `Ck/h` 不变。

所有扰动都是 one-at-a-time 或明确标记的比值保持控制；不根据输出新增网格点。

## 5. 输出

每个场景、引擎和扰动报告：

- 初始瞬时 `dV/dt`，单位 m/s/day。
- 48 小时 `V`、`m`、`Pc`、`RMW`。
- 相对基准的 48 小时差值。
- 0-48 小时轨迹最大绝对差。
- 完整 Markov 的 regime 序列和转移概率最大 L1 差。

跨场景汇总每个常量的最大 `|ΔV|`、`|ΔPc|`、`|ΔRMW|`。图显示固定 regime 的风速差值，避免随机 regime 跳变掩盖连续物理响应。

## 6. 判据

1. [ASSUMED] 若 `Ck` 与 `h` 联合同比缩放后，任一状态轨迹或转移概率差超过 `1e-8`，则“模型只识别 `Ck/h`”被证伪。
2. [ASSUMED] 若完整 Markov 与固定 regime 的风速差相同，确认当前 regime 仍未进入风速方程。
3. [MEASURED] 任一 OAT 扰动引起的输出差按原值报告；不设置事后“可接受”阈值。
4. [MEASURED] regime 序列发生分叉时，连续效应与离散转移放大分别报告。

## 7. 三把刀

1. **状态向量**：`X=(V,m,Pc,RMW)`；完整引擎另含三态 `Z`。
2. **参数与观测**：本轮估计参数为 0；三个文献常量只做外部扰动。三个场景是合成探针，不能承担现实校准。
3. **证伪数据**：数值积分轨迹证伪比值不变性；未来真实回报与登陆真值负责证伪模型性能。本轮只完成结构审计。

## 8. 偏离日志

运行后任何修改单列于此。当前偏离：无。
