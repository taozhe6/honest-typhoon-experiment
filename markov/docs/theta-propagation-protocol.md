# `theta=Ck/h` 终值传播协议

冻结日期：2026-07-15  
状态：`frozen-before-dedicated-theta-grid-output`  
资格：`synthetic-structural-sensitivity`、`unvalidated`

## 已知信息边界

- [CITED] Lin et al. (2023) 开源 FAST 常量为 `Ck=1.2e-3`、西北太平洋
  `h=1800 m`，因此基准 `theta=Ck/h=6.6667e-7 m^-1`。
- [MEASURED] 既有全局敏感性已显示 `Ck` 与 `h` 同比缩放时，全部状态轨迹在
  `1e-8` 容差内不变；模型结构只识别 `Ck/h`。
- [MEASURED] 既有 one-at-a-time 端点已显示 `Ck -30%` 的跨场景最大 48 h
  `abs(delta V)=3.54 m/s`，因此本轮是把已知端点整理成完整 `theta` 网格与终值包络，
  不属于密封性能检验。

## 固定实验

- [ASSUMED] 复用 `config/global_sensitivity.json` 的三个合成 48 h 场景、初始状态、
  6 h 强迫和固定 regime schedule。
- [ASSUMED] 使用固定 regime 引擎。当前 v0.1 的 regime 对风速路径影响为 0；固定日程
  可隔离连续 FAST 方程。
- [ASSUMED] `theta/theta_0` 在闭区间 `[0.7,1.3]` 上取 61 个等距点。
- [MEASURED-DESIGN] 数值实现固定 `h`，只缩放 `Ck`，使比值乘数精确等于网格值。
  端点另以 `h -> h/m` 复算，作为同一比值的参数化不变性检查。
- [ASSUMED] `+/-30%` 是边界情景，没有概率分布含义；输出称为 scenario envelope，
  不称置信区间、预测区间或现实误差条。

## 输出与判据

每个场景保存 61 个 48 h 终值：`V,m,Pc,RMW`，并报告：

- 基准终值；
- `theta=0.7 theta_0` 与 `1.3 theta_0` 的终值；
- 相对基准的两侧差值；
- 端点到端点包络宽度；
- 跨三个场景的最大基准中心绝对 `delta V`。

判据：

1. [MEASURED-DESIGN] `Ck` 参数化和等价 `h` 参数化的端点风速差超过 `1e-8 m/s`，
   证伪“只有 `Ck/h` 进入该实现”的结构结论。
2. [MEASURED-DESIGN] 网格终值超出两个端点终值闭包时，报告非单调响应；包络继续按
   全 61 点最小/最大值计算。
3. [ASSUMED] 与机构 `2--6 m/s` 分歧只比较数量级；两者来源、时间尺度和统计语义不同，
   不进行方差相加，也不解释为总预测误差。

## 三把刀

1. 状态向量：`X=(V,m,Pc,RMW)`；本轮无随机 regime 状态。
2. 参数与观测：拟合参数 0；只扫描一个可识别组合 `theta`；三个场景是合成探针，
   不充当现实观测。
3. 证伪数据：数值参数化不变性与 61 点终值轨迹证伪结构实现；真实预报能力仍需
   独立登陆真值和密封回报。

## 偏离日志

运行后任何场景、网格、引擎或输出定义变化必须记录。当前无偏离。

## 引用

- [Lin et al. (2023), JAMES](https://doi.org/10.1029/2023MS003686)
- [FAST upstream code snapshot](https://github.com/linjonathan/tropical_cyclone_risk/tree/a540a1ed86b121f7244557edb691b40561b81939)

