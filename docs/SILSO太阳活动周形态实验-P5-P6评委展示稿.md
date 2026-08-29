# P5｜科学输出（核心章节）

本节给出三个优先科学假设。三条假设分别对应历史形态关系、跨周期预测增益和观测量信息增量；每条假设均给出物理推导、观测证据、适用条件与可证伪条件。太阳黑子数采用 WDC–SILSO Version 2.0 官方数据产品。

## 5.1 生成的候选假设

| 假设编号 | 假设陈述 | 预期可观测效应 | 置信度与来源 | 验证优先级 |
|---|---|---|---|---|
| H1 | 在统一活动周边界和峰值定义下，上升时间越短，活动周峰值通常越高。 | 上升时间与峰值的 Pearson、Spearman 相关均为负；bootstrap 区间低于 0；逐周期留一与固定分期不改变方向。 | **高：历史描述性证据充分。** 物理推导给出扩散占优条件下的负导数；第 1—24 周统计及多项稳健性检验方向一致。结论不外推为无条件因果律或未来周期预测技能。 | 中 |
| H2 | 极小期大尺度极区场可能改善下一活动周峰值预测。 | 严格按时间顺序的样本外回测中，候选模型误差低于均值与持续性基线；随着新增周期进入，改善应保持同向并逐步收窄不确定区间。 | **中低：已出现方向性增益信号。** Babcock–Leighton/平均场发电机理论给出正向响应；5 个留出折的点估计改善为 13.053，但 95% 区间仍跨 0，尚未达到稳定预测技能标准。 | 高 |
| H3 | 轴向偶极矩相对于普通极区孔径场，可能包含更多与下一活动周峰值有关的信息。 | 在相同目标、样本和滚动留出协议下，轴向偶极矩应稳定降低预测误差，并在孔径、缺测与磁图制度敏感性检查中保持优势。 | **理论自洽，实证暂不可评级。** 两种观测量具有不同数学构造，信息增量可由信噪比与样本外误差直接检验；当前缺少已登记、可复算的全日面磁图与固定球谐产品。 | 高 |

> **优先级说明：** H1 已形成较完整的历史统计基线，后续任务是持续复核；H2、H3 的潜在预测价值更高，且仍存在能够显著改变结论的关键实验，因此验证优先级更高。

## 5.2 理论推导与观测依据

### 5.2.1 总体逻辑：从物理种子场到可观测结果

![图 1｜三个假设的共同物理关系：Polar field 经输运与发电机过程影响 Toroidal field，并映射到活动周形态和峰值；H3 比较 Axial dipole moment 与极区孔径场的预测信息。](figures/silso_hypotheses_mechanism_visio.png)

**图 1 说明：** H1 研究环向场增长与扩散竞争如何映射到活动周形态；H2 研究极小期极向场能否作为下一周期的种子场；H3 比较两种极向场观测量保留的有效信息是否不同。

### 5.2.2 H1：扩散占优条件下的负导数证明

令 $T$ 为从极小期到极大期的有效上升时间，$B_p$ 为活动周初始阶段的有效极向种子场，$B_{\phi,\max}$ 为上升阶段形成的最大环向场，$A_{\max}$ 为太阳黑子活动峰值，$\tau_d$ 为大尺度磁场的有效扩散时间尺度。在最低阶通量输运模型中，差分自转的剪切增长与湍流扩散的衰减可合写为

$$
B_{\phi,\max}(T)=C B_p\Omega T\exp\!\left(-\frac{T}{\tau_d}\right),\qquad C>0.
\tag{1}
$$

对 $T$ 求导得

$$
\frac{\mathrm d B_{\phi,\max}}{\mathrm dT}
=C B_p\Omega\exp\!\left(-\frac{T}{\tau_d}\right)
\left(1-\frac{T}{\tau_d}\right).
\tag{2}
$$

因此，在该模型的扩散占优工作区间 $T>\tau_d$ 内，$\mathrm d|B_{\phi,\max}|/\mathrm dT<0$。若太阳黑子峰值随环向场强度单调增加，$A_{\max}=F(|B_{\phi,\max}|)$ 且 $F'>0$，则由链式法则 $\mathrm dA_{\max}/\mathrm dT<0$。

$$
T>\tau_d,
\tag{3}
$$

$$
\frac{\mathrm d|B_{\phi,\max}|}{\mathrm dT}<0,
\tag{4}
$$

$$
A_{\max}=F(|B_{\phi,\max}|),\qquad F'>0,
\tag{5}
$$

$$
\frac{\mathrm dA_{\max}}{\mathrm dT}<0.
\tag{6}
$$

**自洽性检查：** $T$、$B_p$、$B_{\phi,\max}$、$A_{\max}$ 分别代表增长时间、极向种子场、环向场和黑子峰值，物理量没有循环定义；剪切增长项与扩散衰减项量纲一致；原因方向由增长时间和扩散损失指向最终峰值；式 (2) 的符号与假设方向只在式 (3) 的工作区间内一致。这一步给出了假设方向成立的**充分工作条件**，不是对所有太阳发电机参数区间的无条件断言；通量输运发电机研究表明，高扩散、扩散占优模型能够产生这种 Waldmeier 型关系 [4]。

观测上，第 1—24 个完整活动周的上升时间—峰值关系为 Pearson $r=-0.7495$（$p<0.0001$）和 Spearman $\rho=-0.7619$（$p<0.0001$）；两类 bootstrap 95% 区间分别为 $[-0.8835,-0.5672]$ 和 $[-0.8866,-0.5297]$，且逐周期留一与固定分期检验方向一致。

![图 2｜第 1—24 活动周的周期长度、Rise time、Decline time 与 Peak smoothed sunspot number 关系；中图为 H1 主证据。图例和说明中文化，专业变量名保留英文。](../.morphology-workspace-v24-20260827/projects/default/runs/run_01a0414e-700d-75c3_28ecad54/outputs/cycle_morphology_relationships_zh.png)

**可证伪条件：** 若在统一定义的新增独立活动周中，上升时间与峰值持续呈稳定正向关系；或受观测约束的扩散占优模型仍不能产生上述负导数，则应缩小或撤回 H1 的适用范围。

### 5.2.3 H2：极区场作为下一周期种子场的正向响应证明

平均场磁感应方程写为

$$
\frac{\partial \mathbf B}{\partial t}
=\nabla\times\left(\mathbf v\times\mathbf B+\alpha\mathbf B
-\eta_t\nabla\times\mathbf B\right),
\qquad \nabla\cdot\mathbf B=0.
\tag{7}
$$

把轴对称磁场分解为 $\mathbf B=\nabla\times(A\mathbf e_\phi)+B_\phi\mathbf e_\phi$，差分自转生成环向场的 $\Omega$ 效应源项为

$$
\mathbf B=\nabla\times(A\mathbf e_\phi)+B_\phi\mathbf e_\phi.
\tag{8}
$$

$$
\left.\frac{\partial B_\phi}{\partial t}\right|_\Omega
=r\sin\theta\,(\mathbf B_p\cdot\nabla)\Omega.
\tag{9}
$$

令 $P_n$ 表示第 $n/n+1$ 周期极小期的大尺度极向场指标，$S_\Omega$ 表示有效剪切系数。在一个增长阶段内，可将生成与耗散简化为

$$
\frac{\mathrm dB_\phi}{\mathrm dt}=S_\Omega P_n-\frac{B_\phi}{\tau_d}.
\tag{10}
$$

若极小期初始环向场可以忽略，则

$$
B_\phi(t)=S_\Omega P_n\tau_d
\left[1-\exp\!\left(-\frac{t-t_n}{\tau_d}\right)\right],
\tag{11}
$$

从而

$$
\frac{\partial |B_\phi(t)|}{\partial |P_n|}
=|S_\Omega|\tau_d
\left[1-\exp\!\left(-\frac{t-t_n}{\tau_d}\right)\right]>0.
\tag{12}
$$

若下一活动周峰值满足 $A_{n+1}=F(|B_{\phi,\max}^{(n+1)}|)+\epsilon_n$ 且 $F'>0$，便得到 $\partial A_{n+1}/\partial|P_n|>0$。

$$
A_{n+1}=F\!\left(|B_{\phi,\max}^{(n+1)}|\right)+\epsilon_n,
\qquad F'>0,
\tag{13}
$$

$$
\frac{\partial A_{n+1}}{\partial |P_n|}>0.
\tag{14}
$$

**自洽性检查：** $P_n$ 是式 (9) 中的源场而非任意历史指标；极小期观测先于下一活动周峰值，不存在时间泄漏；极向场—环向场—黑子涌现构成连续物理链条；随机活动区倾角、子午环流和扩散率进入 $\epsilon_n$ 或输运参数，不把极区场写成唯一决定因素。这一物理通道由 Babcock–Leighton 发电机理论和极区场前兆研究支持 [1–3,5–6]。

当前样本外证据使用 10 个相邻活动周对，并对第 20—24 周执行 5 个严格时序留出折。候选模型 MAE 为 **26.972**，优于训练均值基线 **40.026** 和持续性基线 **64.231**，点估计改善 **13.053**；但改善的 95% bootstrap 区间为 **$[-6.999,31.299]$**，仍跨 0。由此支持“已出现方向性增益信号”，尚不足以宣称稳定预测技能。

**可证伪条件：** 若在扩大后的独立时序留出样本中，极区场模型不能持续优于预先锁定的基线，改善区间仍包含 0 或方向反复，则应撤回“稳定预测技能”的表述，并进一步检验发电机记忆、输运参数突变和测量制度差异是否缩小了适用范围。

### 5.2.4 H3：轴向偶极矩与极区孔径场不是同一观测量

令 $\overline{B}_r(\mu,t)$ 为经度平均后的太阳表面径向磁场，$\mu=\cos\theta$，并展开为

$$
\overline{B}_r(\mu,t)=\sum_{\ell=0}^{\infty}a_\ell(t)P_\ell(\mu).
\tag{15}
$$

轴向偶极矩是 $\ell=1,m=0$ 模式的全日面投影：

$$
D(t)=\frac{3}{2}\int_{-1}^{1}\overline{B}_r(\mu,t)\,\mu\,\mathrm d\mu
\propto a_1(t).
\tag{16}
$$

若北极孔径场定义为纬度 $\lambda\ge\lambda_0$ 内的平均，$\mu_0=\sin\lambda_0$，则

$$
P_{\mathrm{cap},N}(t)
=\frac{1}{1-\mu_0}\int_{\mu_0}^{1}\overline{B}_r(\mu,t)\,\mathrm d\mu
=\sum_\ell w_\ell(\mu_0)a_\ell(t).
\tag{17}
$$

因此一般有 $D\ne P_{\mathrm{cap}}$：$D$ 只提取全局偶极模式，$P_{\mathrm{cap}}$ 会混合 $\ell=1,3,5,\ldots$ 等多个模态，并随人为选定的纬度孔径变化。两者是不同的物理构造，而非同一指标的不同名称。

$$
D\ne P_{\mathrm{cap}}.
\tag{18}
$$

若不同轴对称模态向下一周期环向场的传递增益为 $G_\ell$，则 $B_\phi^{(n+1)}\simeq\sum_\ell G_\ell a_\ell^{(n)}+\xi_n$。高阶模态空间尺度更短，通常更容易受到磁扩散衰减。当极小期满足 $|G_1a_1|\gg|\sum_{\ell\ge3}G_\ell a_\ell|$ 时，$D\propto a_1$ 比混合多个模态的孔径平均更接近发电机能够保存和放大的有效种子场。相关研究将轴向偶极矩与极区孔径场视为相关但不同的前兆量，并讨论了孔径定义的任意性及轴向偶极矩的潜在优势 [6–7]。

$$
B_\phi^{(n+1)}\simeq\sum_\ell G_\ell a_\ell^{(n)}+\xi_n,
\tag{19}
$$

$$
|G_1a_1|\gg\left|\sum_{\ell\ge3}G_\ell a_\ell\right|.
\tag{20}
$$

**自洽性检查：** $D$ 与 $P_{\mathrm{cap}}$ 的定义分别由全日面偶极投影和固定纬度窗口平均给出；“额外信息”被定义为相同目标、相同样本外协议下的信噪比或误差差异；高阶模态混合提供潜在信息损失的物理来源；因此物理合理性不会被直接写成实验结果。

“额外信息”可以进一步写成可直接检验的信噪比条件。若 $A_{n+1}=\beta a_1+\epsilon$，且 $D=c_1a_1+\nu_D$、$P_{\mathrm{cap}}=w_1a_1+\sum_{\ell\ge3}w_\ell a_\ell+\nu_P$，则

$$
A_{n+1}=\beta a_1+\epsilon.
\tag{21}
$$

$$
D=c_1a_1+\nu_D,
\tag{22}
$$

$$
P_{\mathrm{cap}}=w_1a_1+\sum_{\ell\ge3}w_\ell a_\ell+\nu_P.
\tag{23}
$$

$$
\mathrm{SNR}_D=\frac{c_1^2\operatorname{Var}(a_1)}{\operatorname{Var}(\nu_D)},
\tag{24}
$$

$$
\mathrm{SNR}_{P}=\frac{w_1^2\operatorname{Var}(a_1)}
{\sum_{\ell\ge3}w_\ell^2\operatorname{Var}(a_\ell)+\operatorname{Var}(\nu_P)}.
\tag{25}
$$

只有当

$$
\mathrm{SNR}_D>\mathrm{SNR}_{P}
\tag{26}
$$

并且轴向偶极矩在同一样本外协议下取得稳定、可复现的误差下降，才可认定其提供额外预测信息。当前尚无已登记、可复算的全日面磁图与固定球谐产品，因此此处只完成物理与数学可检验性证明，不报告实证增益。

**可证伪条件：** 若轴向偶极矩在统一样本外比较中不优于极区孔径场，或

$$
\mathrm{SNR}_D\le\mathrm{SNR}_{P}
\tag{27}
$$

且差异稳定落入预先规定的等效范围，则应撤回“提供额外预测信息”的部分；这一结果不否定 $D\ne P_{\mathrm{cap}}$ 所表达的观测量物理区别。

### 5.2.5 证据汇总与适用范围

| 假设 | 理论依据 | 观测或计算证据 | 当前可支持的结论 | 尚不能支持的结论 |
|---|---|---|---|---|
| H1 | 通量输运发电机中剪切增长与扩散衰减的竞争；扩散占优工作区间内导数为负 [4] | WDC–SILSO 第 1—24 周逐周期表、相关系数、bootstrap、逐周期留一、固定分期与散点图 | 当前官方定义和历史样本中存在稳健负相关 | 无条件因果机制；未来活动周预测技能 |
| H2 | 平均场磁感应方程、$\Omega$ 效应与 Babcock–Leighton 种子场通道 [1–3,5–6] | MWO/WSO 极区场、10 个相邻周期对、5 个严格时序留出折、均值与持续性双基线 | 存在有利的方向性样本外增益信号 | 已经获得稳定预测技能；极区场是唯一决定因素 |
| H3 | 偶极投影与孔径平均的独立定义；模态传递和信噪比条件 [6–7] | 当前完成数据可得性核对；样本外比较尚未执行 | 观测量确有物理区别，信息增量具有清楚的检验条件 | 轴向偶极矩已经优于孔径场；已经获得预测增益 |

## 5.3 反例与不支持证据

| 反例或限制 | 相关假设 | 来源与形式 | 对假设的修正意义 |
|---|---|---|---|
| H1 的负导数依赖扩散占优工作区间；历史相关本身不能证明太阳内部始终满足该条件。 | H1 | 独立物理论证与模型条件检查 | 将 H1 表述限定为“有条件的机制解释 + 高置信历史关系”，不升级为无条件因果律。 |
| H1 仅包含 24 个完整活动周；长期测量制度变化、活动周依赖和小样本都会影响相关强度。 | H1 | WDC–SILSO 逐周期统计与敏感性分析 | “高置信”仅适用于当前样本、变量定义和历史描述任务。 |
| H2 的改善区间 $[-6.999,31.299]$ 跨 0，5 个留出折中有 1 折改善为负；MWO 仅贡献 1 个测试折。 | H2 | 严格时间顺序样本外回测 | 保留方向性增益信号，暂不授予稳定预测技能。 |
| 子午环流、扩散率、随机活动区倾角和测量制度会改变极区场到下一周期峰值的映射。 | H2 | 物理适用条件与分制度检查 | 将极区场定位为重要前兆量，而非下一峰值的唯一决定因素。 |
| 尚无已登记的轴向偶极矩产品或可复算全日面磁图，普通极区孔径场不能替代该输入。 | H3 | 数据登记与复算条件检查 | 保留研究价值；当前结论为“实证待评估”。 |

## 5.4 下一步验证计划

| 假设编号 | 验证方法 | 所需数据或设施 | 预期周期 | 成功判据 |
|---|---|---|---|---|
| H1 | 按相同官方定义接入新增完整活动周，重算相关、bootstrap、逐周期留一与分时期结果；同步用受观测约束的输运参数检验负导数工作区间。 | WDC–SILSO 后续官方极值与平滑序列；受观测约束的通量输运模型 | 新完整活动周形成后 | 负方向保持，区间仍位于 0 以下，且模型在合理参数区间复现负导数；否则降低或撤回限域支持。 |
| H2 | 锁定当前预测形式与双基线，扩大滚动时序留出窗口，并分别报告 MWO、WSO 测量制度下的结果。 | 同口径 MWO/WSO 极区场及后续官方峰值 | 新目标活动周完成后 | 多个新增留出折持续优于两类基线，改善区间排除 0，分制度结果方向不反转。 |
| H3 | 注册可复算全日面磁图，固定球谐截断、极区孔径、缺测填补和时间窗；在完全相同的滚动留出协议下比较 $D$ 与 $P_{\mathrm{cap}}$。 | 全日面磁图、覆盖说明、固定球谐算法、缺测规则与独立测试周期 | 数据登记完成后启动 | 轴向偶极矩在独立留出周期稳定降低误差，$\mathrm{SNR}_D>\mathrm{SNR}_P$，且结论通过孔径、缺测和磁图制度敏感性检查。 |

# P6｜代表性结果与适用范围

## 6.1 H1：扩散占优机制与历史关系

针对“上升更快的活动周是否具有更高峰值”这一问题，将上升时间、种子场、环向场、峰值与扩散时间尺度定义为相互独立的物理量。由剪切增长和扩散衰减的竞争可得，在 $T>\tau_d$ 条件下，环向场强度及其对应的活动周峰值随上升时间增加而下降。基于 WDC–SILSO 的统一周期边界和峰值定义，第 1—24 周逐周期数据的 Pearson、Spearman、bootstrap、逐周期留一和固定分期检验均支持同一方向。

Pearson 相关系数为 $r=-0.7495$，Spearman 秩相关系数为 $\rho=-0.7619$；两类 bootstrap 区间均低于 0，逐周期留一和固定分期也未改变方向。因此，在当前官方定义和历史样本范围内，H1 得到稳健的描述性支持。该结果不等同于无条件因果律，也不代表未来活动周的预测技能。

| 分析层次 | 主要结果 |
|---|---|
| 假设生成 | 上升时间越短，活动周峰值通常越高 |
| 物理推导 | $B_{\phi,\max}=CB_p\Omega T e^{-T/\tau_d}$；在 $T>\tau_d$ 时导数为负 |
| 数据证据 | WDC–SILSO 第 1—24 周；Pearson $r=-0.7495$，Spearman $\rho=-0.7619$ |
| 稳健性 | 两类 bootstrap 区间低于 0；逐周期留一和固定分期不改变方向 |
| 结论边界 | 高置信历史关系；不是无条件因果律，也不是未来周期预测技能 |
| 撤回条件 | 新独立周期持续呈稳定正向关系，或受观测约束模型不能复现负导数 |

## 6.2 H3：轴向偶极矩命题的观测条件

H3 的物理推导表明，轴向偶极矩 $D$ 与普通极区孔径场 $P_{\mathrm{cap}}$ 具有不同的数学定义，并给出 $\mathrm{SNR}_D>\mathrm{SNR}_P$ 与样本外误差下降两个可检验条件。当前数据包含普通极区孔径场，但尚缺少可复算的全日面磁图和固定球谐产品，因此不能据此报告轴向偶极矩的实证增益。两种观测量应在相同目标、样本和滚动留出协议下直接比较。

由此，H3 的当前结论为“理论自洽、研究价值高、实证待评估”。后续验证应先完成全日面磁图登记，固定球谐截断、极区孔径、缺测填补和时间窗，再在完全相同的滚动留出协议下比较 $D$ 与 $P_{\mathrm{cap}}$。

## 理论依据参考文献

1. Babcock, H. W. (1961). The Topology of the Sun's Magnetic Field and the 22-Year Cycle. *The Astrophysical Journal*, 133, 572–587. https://doi.org/10.1086/147060
2. Leighton, R. B. (1969). A Magneto-Kinematic Model of the Solar Cycle. *The Astrophysical Journal*, 156, 1–26. https://doi.org/10.1086/149943
3. Schatten, K. H., Scherrer, P. H., Svalgaard, L., & Wilcox, J. M. (1978). Using Dynamo Theory to Predict the Sunspot Number during Solar Cycle 21. *Geophysical Research Letters*, 5, 411–414. https://doi.org/10.1029/GL005i005p00411
4. Karak, B. B., & Choudhuri, A. R. (2011). The Waldmeier Effect and the Flux Transport Solar Dynamo. *Monthly Notices of the Royal Astronomical Society*, 410, 1503–1512. https://doi.org/10.1111/j.1365-2966.2010.17531.x
5. Hathaway, D. H., & Upton, L. A. (2016). Predicting the Amplitude and Hemispheric Asymmetry of Solar Cycle 25 with Surface Flux Transport. *Journal of Geophysical Research: Space Physics*, 121, 10744–10753. https://doi.org/10.1002/2016JA023190
6. Upton, L. A., & Hathaway, D. H. (2023). Solar Cycle Precursors and the Outlook for Cycle 25. *Journal of Geophysical Research: Space Physics*, 128, e2023JA031681. https://doi.org/10.1029/2023JA031681
7. Pal, S. (2026). Which Polar Precursor Better Predicts Solar Cycles: Axial Dipole Moment or Hemispheric Polar Flux of the Sun? *The Astrophysical Journal*, 1000, 189. https://doi.org/10.3847/1538-4357/ae4c5a
