# Typhoon Markov Reduced-Order Model

当前状态：`research-rejected`。巴威结构审计证明三种隐状态对风速趋势的作用恒为零；v0.1 已失去风速预测校准资格，保留用于复现失败。

项目目标与永久规则以[项目原文](../README.md)为准。

## 支线 C 零标签基线

[支线 C 发布报告](report_c_branch.md)已完成：IBTrACS 自动波形事件、风暴分组折外 Brier、公开 CE/ERC 资源审计、Kuo et al. 62 行 CE formation 表，以及巴威 CyclObs `V·R` 时序。主结果证伪了持续性概率增益；巴威 SAR 支持一个观测双环时段。

[巴威一手覆盖纠错报告](report_c_coverage_correction.md)已经撤回旧版 ERC 数量否定：CyclObs 确证 1 个连续双环风结构观测时段；7 月 4 日和 7 日两个二手待核窗口不可判定；TC PRIMED WP09 在审计时无 preliminary 文件。二手叙述与一手 tally 完全分栏。

[C 事件标签 v2 报告](report_c_event_label_v2.md)把物理波形标签冻结为未来
24 h 内先减弱、后恢复且两段均至少 `2.5 m/s`。开发期发生率为
`5.9%` [4.7%, 7.2%]，密封验证期为 `14.4%` [9.9%, 19.4%]；
验证集同时含正负类。气候 Brier 为 `0.130603`，持续性 Brier 为
`0.131092`，持续性基线略差。该标签是强度波形代理，ERC 因果字段保持为空。

```bash
cd "/Users/taozhe/Documents/New project/typhoon"
markov/.venv/bin/python markov/scripts/run_c_branch.py
```

## FAST 固定常量敏感性

[全局敏感性发布报告](report_global_sensitivity.md)已完成：`Ck`、边界层深度 `h`、FAST `kappa` 分别运行 `+/-30%`，覆盖三个冻结合成场景。结构控制确认当前连续核心只识别 `Ck/h`；三态 regime 对风速路径的作用仍为 `0.0 m/s`。

[`theta=Ck/h` 终值传播报告](report_theta_propagation.md)进一步用 61 点网格把
可识别组合传播到 48 h 最终风速。三个合成场景的最大单侧变化为
`3.54 m/s`，最大端点宽度为 `6.07 m/s`；该区间属于有界压力测试，
概率分布语义为空。

```bash
cd "/Users/taozhe/Documents/New project/typhoon"
markov/.venv/bin/python markov/scripts/run_global_sensitivity.py
```

```bash
cd "/Users/taozhe/Documents/New project/typhoon/markov"
.venv/bin/python scripts/run_theta_propagation.py
```

## 巴威证伪结果

- NMC 2 分钟通道：`40 -> 42 m/s`，12 小时预报命中后续分析。
- JTWC 1 分钟通道：`75 -> 85 kt`，12 小时预报维持 `75 kt`，低估 `10 kt`。
- JMA 10 分钟通道：`85 -> 80 kt`，12 小时预报维持 `85 kt`，高估 `5 kt`。
- v0.1 反事实导数：三个 regime 的 `dV/dt` 完全相等，最大差为 `0.0 m/s/day`。

三家机构均在原生风速平均时段内独立评分。跨机构评分等待带不确定性的观测算子。

## 模型边界

- 状态：`X=(V, m, Pc, RMW)`，外加三态隐马尔可夫机制。
- 评分输出字段：最大风、中心气压、RMW；三字段依赖性单独审计。
- 全局可调参数：2 个；物理常量锁定并记录来源。
- 强迫：完整廓线 PI、风切、中层熵亏缺、混合层深度/层结、平移速度、表面粗糙度、陆地覆盖。
- 求解：Dormand--Prince RK45，方法族与 `ode45` 相同。

研究证据见 [research.md](docs/research.md)，ERC 数据源与巴威 SAR 审计见 [erc-microwave-data-source-audit.md](docs/erc-microwave-data-source-audit.md)，方程见 [mathematical-model.md](docs/mathematical-model.md)，淘汰门槛见 [falsification-protocol.md](docs/falsification-protocol.md)，架构决策见 [ADR-001](docs/ADR-001-multistate-markov-core.md)。

## 运行测试

```bash
cd "/Users/taozhe/Documents/New project"
PYTHONPATH=typhoon/markov/src python3 -m unittest discover -s typhoon/markov/tests -v
```

## 合成烟雾测试

```bash
cd "/Users/taozhe/Documents/New project/typhoon/markov"
PYTHONPATH=src python3 scripts/run_synthetic.py
```

输出文件只包含合成场景，并带 `authoritative_forecast=false` 标志。

## 重建并运行巴威案例

```bash
cd "/Users/taozhe/Documents/New project"
PYTHONPATH=typhoon/markov/src python3 typhoon/markov/scripts/build_bavi_reintensification_case.py
PYTHONPATH=typhoon/markov/src python3 typhoon/markov/scripts/run_bavi_reintensification_case.py
```

构建器从 NMC 历史详情、GDACS 原始 GTS 报文和 UCAR ATCF 归档取数，并把整页响应与入选证据 SHA-256、时次和每一环的平均风窗写入案例文件。

## 审计巴威 SAR 双风峰

```bash
cd "/Users/taozhe/Documents/New project"
python3 -m pip install -r typhoon/markov/requirements-research.txt
python3 typhoon/markov/scripts/audit_cyclobs_structure.py \
  --sid wp092026 \
  --start-date 2026-07-01 \
  --stop-date 2026-07-13 \
  --output typhoon/markov/outputs/bavi_2026_cyclobs_structure_audit.json
```

脚本按 SID 动态查询 CyclObs，保存每景来源哈希、质量标志、完整轴对称风速廓线和探索性双峰候选。科学阈值来自机器可读观测契约。

## 目录

- `config/model_v0.json`：参数预算、固定常量、质控和证伪门槛。
- `config/wind_observation_contract.json`：NMC/JMA/JTWC 风速定义与平均时段契约。
- `config/erc_observation_sources.json`：微波、ARCHER 和 SAR 官方数据源注册表。
- `config/eyewall_structure_observation_contract.json`：双风环观测语义、质量门槛和反泄漏规则。
- `config/global_sensitivity.json`：FAST 固定常量敏感性的冻结场景、扰动和结构判据。
- `config/theta_propagation.json`：`theta=Ck/h` 的冻结 61 点终值传播设计。
- `config/c_event_label_v2.json`：5--15% 事件率目标、时间密封和 Brier 基线规则。
- `cases/bavi_2026_reintensification_spec.json`：冻结的案例选择规则和时次。
- `cases/bavi_2026_reintensification.json`：从归档报文生成的案例证据。
- `src/typhoon_markov/model.py`：状态、FAST 核心、转移核、气压/RMW 方程。
- `src/typhoon_markov/case_validation.py`：原生通道评分、跨口径闸门和结构审计。
- `src/typhoon_markov/sensitivity.py`：固定 regime 与完整 Markov 的常量敏感性运行器。
- `src/typhoon_markov/rk45.py`：自适应 Dormand--Prince 5(4)。
- `src/typhoon_markov/audit.py`：识别性与回报检验闸门。
- `scripts/audit_cyclobs_structure.py`：CyclObs 动态发现、NetCDF 径向剖面与双峰审计。
- `scripts/audit_erc_source_availability.py`：TC PRIMED、CyclObs 与 ARCHER 实际库存审计。
- `tests/`：解析解、马尔可夫性、物理单调性和审计测试。
- `outputs/bavi_2026_reintensification_case.json`：当前模型的巴威案例判决。
- `outputs/bavi_2026_cyclobs_structure_audit.json`：巴威 SAR 双风峰来源与径向廓线审计。
- `outputs/erc_source_availability_2022_2026.json`：密封期与当前风暴的数据源库存、请求 URL 和响应哈希。
- `outputs/global_sensitivity/`：逐步轨迹、场景级汇总、图和 SHA-256 manifest。
- `outputs/theta_propagation/`：可识别组合的 48 h 终值网格、图和 manifest。
- `outputs/c_event_label_v2/`：事件标签选择、密封验证、可靠性图和 manifest。
- `../previous/2026-07-11-scalar-lgem/`：此前模型的只读归档。
