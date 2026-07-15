# 数学模型规格 v0.1

## 0. 结构审计判决

v0.1 状态为 `research-rejected`。现有方程满足

\[
\dot V=f_{FAST}(V,m,U),
\qquad \frac{\partial \dot V}{\partial Z}=0.
\]

`Z` 只进入 `Pc` 方程。给定相同初值与强迫，ODE 解的唯一性给出

\[
V_{Z=-1}(t)=V_{Z=0}(t)=V_{Z=+1}(t).
\]

两组数值反事实探针得到三态风速趋势最大差 `0.0 m/s/day`。因此三个标签无法承担风速“减弱 / 准稳态 / 增强”机制，v0.1 停止校准。下一版结构必须让隐状态进入风速发射方程，同时重新登记参数预算和灵敏度秩。

## 1. 状态、机制与强迫

以 6 小时为一个马尔可夫时段：

\[
X_k=(V_k,m_k,P_{c,k},R_k),\qquad
Z_k\in\{-1,0,+1\}.
\]

`V` 为去除平移分量后的近地面轴对称最大风，`m` 为 0--1 内核水汽状态，`Pc` 为中心气压，`R` 为 RMW。v0.1 曾将 `Z` 命名为减弱、准稳态、增强机制；结构审计已经撤销该解释。

外部强迫

\[
U_k=(V_p,S,\chi_g,h_m,\Gamma,u_T,c_s,L,\varphi)
\]

依次为完整廓线 PI、250--850 hPa 风切、中层饱和熵亏缺、混合层深度、混合层下方温度层结、平移速度、表面交换倍率、陆地覆盖率和纬度。

一阶马尔可夫条件为

\[
p(X_{k+1},Z_{k+1}\mid X_{0:k},Z_{0:k},U_{0:k})
=p(X_{k+1},Z_{k+1}\mid X_k,Z_k,U_k).
\]

代码接口只接收当前状态、当前机制和当前强迫，这一条件由 API 和测试共同锁定。

## 2. 环境依赖转移核

先由 FAST 当前趋势定义无量纲分数

\[
q_k=\frac{86400\,\dot V_{FAST}}{30\ \mathrm{kt\,day^{-1}}}.
\]

三个固定标签中心为 `c=(-1,0,+1)`。转移概率为

\[
P(Z_{k+1}=j\mid Z_k=i,X_k,U_k)
\propto w_j(L)\exp\{a(1-L)\,1(i=j)-(q_k-c_j)^2/(2s^2)\},
\]

其中 `w_{增强}(L)=1-L`，其余两个权重为 1。登陆过程按海面占比削弱机制记忆；全陆地下增强态权重为零。`a=persistence_logit`、`s=regime_width` 是仅有的两个全局拟合参数。

## 3. `V-m` 物理核心

采用 FAST：

\[
\frac{dV}{dt}=\frac{C_k c_s}{2h}
\left[\alpha\beta(1-L)V_p^2m^3-(1-\gamma m^3)V^2\right],
\]

\[
\frac{dm}{dt}=\frac{C_k c_s}{2h}
\left[(1-m)V-\chi_gSm\right],
\]

\[
\beta=1-\epsilon-\kappa,\qquad
\gamma=\epsilon+\alpha\kappa.
\]

海洋反馈为

\[
\alpha_o=1-0.87e^{-z},\qquad
z=0.01\Gamma^{-0.4}h_mu_TV_p/V,
\]

并用 `alpha=(1-L) alpha_o+L` 处理部分陆地覆盖。能量输入项使用 `(1-L)Vp^2`，对应开放水面面积缩放。

`Ck=1.2e-3`、WP `h=1800 m`、`epsilon=0.33`、`kappa=0.1` 固定。`c_s` 仅由表面粗糙度资料生成。

## 4. 气压与尺度

Sparks--Toumi 短时气压方程：

\[
\frac{dP_c}{dt}=-\frac{2P_c\xi(Z)}{R},
\]

其中 `xi(-1)=-0.24 km/day`、`xi(0)=0`、`xi(+1)=0.29 km/day`。

RMW 使用绝对角动量零阶闭合

\[
M=RV+\frac12 fR^2,\qquad \dot M=0,
\]

得到

\[
\frac{dR}{dt}=-\frac{R}{V+|f|R}\frac{dV}{dt}.
\]

`dot M=0` 是可被 RMW 回报检验推翻的结构假设。每个风暴的额外角动量修正参数数目固定为 0。

## 5. 观测方程与初始化

评分观测为

\[
Y_k=HX_k+\varepsilon_k,\qquad
H=\begin{bmatrix}
1&0&0&0\\
0&0&1&0\\
0&0&0&1
\end{bmatrix}.
\]

三行对应 `V、Pc、RMW`。训练采用 JTWC 同一套 1 分钟风、气压和 RMW 字段；原始最大风先依据平移速度生成轴对称目标。CMA/JMA/HKO 保留为跨机构外部验证，平均时段写入每条观测元数据。

`m0` 由起报前观测趋势解析反演：

\[
m_0^3=\frac{2h\dot V_{past}/(C_kc_s)+V_0^2}
{\alpha\beta(1-L)V_p^2+\gamma V_0^2}.
\]

反演值超出容许范围时，初始化失败并终止该样本。初始化资料的截止时刻为起报时刻。

## 6. 数值解

每个 6 小时区间将强迫视为分段常数，使用 Dormand--Prince 5(4) 自适应积分，方法族与 MATLAB `ode45` 相同。当前设置 `rtol=1e-6`、最大内部步长 900 秒；误差控制分别按 `V,m,Pc,R` 的量纲设置绝对容差。
