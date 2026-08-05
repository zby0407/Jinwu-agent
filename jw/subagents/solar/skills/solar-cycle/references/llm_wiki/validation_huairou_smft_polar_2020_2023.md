# 怀柔 SMFT 2020–2023 极区数据生产门槛审计

审计日期：2026-08-04。结论：**未通过生产门槛**。现有日表、月表和中心圆
结果只能作为文件解码与工程流程诊断，不能作为固定日面纬度的有符号极区磁场。

## 本地证据

归档中 1151 个 `npl`/`spl` 候选文件均为两平面 `992×992`、`BITPIX=32`、
`IMPERX 1M48`，`CALIBRAT=10000`。分层抽取 2020 N、2022 N/S、2023 N/S
各 12 帧，共 60 帧：

| 门槛 | 证据 | 状态 |
|---|---|---|
| P0/P1 配准 | 平面相关系数最小 0.998249；平移模长 P95 为 0.001314 像素 | 通过 |
| P0/P1 符号 | 头中无平面含义/偏振手性；本地无有符号外部参考 | 未通过 |
| `CALIBRAT` 含义 | 数值恒为 10000；头中无单位、误差、方法或适用范围 | 未通过 |
| 标准太阳 WCS | 60/60 缺 `CTYPE1/2, CRPIX1/2, CDELT1/2` | 未通过 |
| 固定纬度掩膜 | 中心圆盘内比例范围 0.17695–1.0；明显随指向改变 | 未通过 |
| 同日 SOLIS/HMI 符号 | 本地无对照文件；按要求不下载 | 未通过 |

代表帧的强度边缘可用二次曲线稳定拟合，残差中位数约 0.3–0.4 像素，说明
“从日缘恢复几何”有技术可行性。但由短圆弧得到的太阳半径约 3400–3600 像素，
与头字段 `SIZE_PIX='0.242*2.242 ARC.'` 不能自洽；在外部配准或仪器几何说明
缺失时，不能据此宣称获得了可验证的日面纬度。

## 文献约束

SMFT 原始量是左右圆偏振强度，定义为
`V/I=(Vl-Vr)/(Vl+Vr)`、`I=Vl+Vr`。弱场线性标定写作
`B_parallel=C_parallel*(V/I)`；历史工作确有 `C_L=10000 G` 的系数，但也有
其他系数与非线性改进，且单点弱场标定在强场会饱和。因此，本地头中的
`CALIBRAT=10000` 与“线性纵场系数”一致，却不足以证明 P0/P1 顺序、最终符号
或跨时期绝对尺度。

固定纬度对照应参考 SOLIS 的公开定义：先在径向场假设下由 LOS 转为径向场，
再按 60–70°、65–75° 或 60–75° 纬带及中央经度 ±50° 取样，并显式考虑 B0
可见性。当前中心像素圆不满足这个定义。

## 解锁生产所需证据

1. 仪器/数据格式说明明确 P0/P1 与 `Vl/Vr` 的映射，或用本地同日 HMI
   有符号磁图通过共配准直接确定映射。
2. 明确 `CALIBRAT` 的单位、生成方法、适用波长位置及观测时期；至少与弱场
   区域 HMI 回归的符号、相关系数和斜率相符。
3. 由仪器说明或外部连续谱共配准确定像元尺度、相机滚转、盘心/日缘，结合
   观测时刻的 B0/P 角生成逐像素日面经纬度。
4. 固定纬带掩膜通过可视化边界、有效像素数和跨帧稳定性测试。
5. 在多个同日 N/S 样本上验证 SMFT 与 HMI 的区域平均符号一致；SOLIS 对
   2020–2023 无同日覆盖时应标为不适用，而非通过。

在这些证据齐备前，加载器默认拒绝覆盖同一后期 IMPERX 格式的
`imperx_fit32_2018_2026`。只有显式传入
`--allow-unvalidated-geometry` 才能生成诊断输出。

本地审计可复现为：

```powershell
python jw\subagents\solar\skills\solar-cycle\scripts\validate_polar_huairou.py `
  --polar-dir "D:\极区前兆" --sample-per-group 12 `
  --output artifacts\polar_validation\audit.json `
  --records-output artifacts\polar_validation\audit_records.csv
```

同日 HMI 文件由用户在本地提供后，用
`huairou_hmi_manifest.example.json` 复制出实际清单。每对记录包含 SMFT 两平面
FITS、HMI 720 s LOS 磁图、HMI 连续谱、半球，以及至少 3 对对应控制点（坐标顺序
均为 `x, y`）；也可直接提供从 SMFT 到 HMI 像素的 2×3 仿射矩阵。验证器不会
访问网络：

```powershell
python jw\subagents\solar\skills\solar-cycle\scripts\validate_polar_huairou_hmi.py `
  --manifest D:\local_hmi\huairou_hmi_manifest.json `
  --output artifacts\polar_validation\hmi_reference_audit.json
```

验证器利用连续谱梯度相关独立检查仿射配准；通过 HMI WCS、`CRLT_OBS` 和
`RSUN_OBS` 计算逐像素日面纬度、中央经度和 `mu`；在 60–75°、中央经度
±50° 的固定纬带中，分别检验 `(P0-P1)/(P0+P1)` 两种符号、区域平均极性，
以及 HMI LOS 对 `V/I` 的弱场回归斜率。默认要求南北半球各至少 3 对非模糊
同日样本，且每对文件的观测时刻相差不超过 120 分钟。该实现补齐了本地验证入口，但在真实 HMI 文件运行并通过前，不改变
本报告的“未通过”结论，也不会解锁生产加载器。

## 主要资料

- Xu et al. (2021), RAA 21, 67: SMFT `Vl/Vr` 与 `V/I` 定义。
- Plotnikov et al. (2019): `B_parallel=C_parallel*(V/I)` 与弱场饱和问题。
- Bai et al. (2014), MNRAS 445, 49: SMFT/HMI 共配准与标定比较。
- NSO SOLIS/VSM Polar Magnetic Field Data: 固定纬带、中央经度和 B0 权重定义。
