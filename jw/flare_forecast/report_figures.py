"""Full-cohort figures rendered by the native forecast tool, without refitting."""

from datetime import datetime

import numpy as np

from .assets import sha
from .native_data import write

TARGETS = (("C", "C1.0+", "label_c1"), ("M", "M1.0+", "label_m1"))
COLORS = ("#087f8c", "#8293a7", "#d89a35", "#b7c3d2")


def render_figures(forecast, rows, predictions, reference, output):
    from matplotlib.figure import Figure

    records = []
    bins_output = []

    def figure(size, shape):
        fig = Figure(figsize=size, layout="constrained")
        return fig, fig.subplots(*shape)

    def save(fig, stem, title, scope="historical", n=None):
        for ext in ("png", "svg"):
            fig.savefig(output / f"{stem}.{ext}", dpi=160)
        records.append(
            {
                "id": stem,
                "title": title,
                "scope": scope,
                "sample_count": len(rows) if n is None else n,
                "png_sha256": sha(output / f"{stem}.png"),
                "svg_sha256": sha(output / f"{stem}.svg"),
            }
        )
        fig.clear()

    def arrays(target, label):
        return np.array([int(r[label]) for r in rows]), np.array(
            [r[target] for r in predictions]
        )

    fig, ax = figure((10, 3.8), (1, 1))
    values = [forecast["probabilities"][t] for _, t, _ in TARGETS]
    ax.barh([t for _, t, _ in TARGETS], values, color=COLORS[:2], height=0.5)
    for i, p in enumerate(values):
        ax.text(min(p + 0.02, 0.84), i, f"{p:.2%}", va="center", fontsize=14)
    ax.axvline(0.5, linestyle="--", color="#b04f45", label="Fixed threshold 0.5")
    ax.set(
        xlim=(0, 1),
        xlabel="At least one new-start event in the issued 24h window",
        title="Current prospective experiment: probabilities, not accuracy",
    )
    ax.legend(loc="lower right")
    save(fig, "current", "本次24小时发生概率", "current prospective window", 1)

    dates = [
        datetime.fromisoformat(r["target_start_utc"].replace("Z", "+00:00"))
        for r in rows
    ]
    fig, axes = figure((12, 7), (2, 1))
    for ax, (_, target, label) in zip(axes, TARGETS, strict=True):
        y, p = arrays(target, label)
        for value, color, marker in ((0, "#447dba", "x"), (1, "#dc6357", "o")):
            selected = y == value
            ax.scatter(
                np.array(dates)[selected],
                p[selected],
                s=15,
                alpha=0.65,
                color=color,
                marker=marker,
                label=f"Observed {'yes' if value else 'no'}: n={selected.sum()}",
            )
        ax.axhline(0.5, color="gray", linestyle="--", linewidth=0.8)
        ax.set(
            ylim=(-0.04, 1.05),
            ylabel=f"{target} probability",
            title=f"Every historical test window: n={len(rows)}",
        )
        ax.legend(fontsize=9, ncols=2, loc="lower left")
    axes[-1].set_xlabel(
        "Historical window start (UTC), 2023-2025; missing dates are not filled"
    )
    save(fig, "all_samples", "全部独立测试窗口的概率散点")

    fig, axes = figure((12, 7), (2, 3))
    models = ("selected_calibrated", "train_climatology", "always_yes", "always_no")
    names = ("SHARP RF", "Train climate", "Always yes", "Always no")
    for i, (_, target, _) in enumerate(TARGETS):
        for j, metric in enumerate(("Accuracy", "Brier", "TSS")):
            ax = axes[i, j]
            values = [
                float(
                    next(
                        r
                        for r in reference["metrics"]
                        if r["target"] == target and r["model_id"] == model
                    )[metric]
                )
                for model in models
            ]
            ax.bar(range(4), values, color=COLORS)
            ax.set_xticks(range(4), names, rotation=25, ha="right", fontsize=9)
            ax.set_title(
                f"{target}: {metric} ({'lower' if metric == 'Brier' else 'higher'} is better)",
                fontsize=10,
            )
            comparison = [
                float(r[metric])
                for r in reference["metrics"]
                if r["model_id"] in models
            ]
            low = min(0, min(comparison) * 1.2)
            high = 1.13 if metric == "Accuracy" else max(0.05, max(comparison) * 1.25)
            ax.set_ylim(low, high)
            for k, value in enumerate(values):
                ax.text(
                    k,
                    value + (high - low) * 0.02,
                    f"{value:.3f}",
                    ha="center",
                    fontsize=9,
                )
    save(fig, "baselines", "同日期RF与常数基线性能比较")

    fig, axes = figure((10, 4.7), (1, 2))
    for ax, (_, target, label) in zip(axes, TARGETS, strict=True):
        y, p = arrays(target, label)
        cm = np.array(
            [[np.sum((y == i) & ((p >= 0.5) == j)) for j in range(2)] for i in range(2)]
        )
        ax.imshow(cm, cmap="Blues", vmin=0, vmax=max(1, cm.max()))
        ax.set_xticks([0, 1], ["Forecast no", "Forecast yes"])
        ax.set_yticks([0, 1], ["Observed no", "Observed yes"])
        ax.set_title(f"{target}: n={len(rows)}, threshold=0.5")
        for i in range(2):
            for j in range(2):
                fraction = cm[i, j] / cm[i].sum() if cm[i].sum() else 0
                ax.text(
                    j,
                    i,
                    f"{cm[i, j]} windows\n{fraction:.1%} of class",
                    ha="center",
                    va="center",
                    fontsize=12,
                    color="white" if cm[i, j] > cm.max() * 0.55 else "#162535",
                )
    save(fig, "confusion", "检出、漏报与误报混淆矩阵")

    fig, axes = figure((11, 7.5), (2, 2))
    edges = np.linspace(0, 1, 11)
    for j, (_, target, label) in enumerate(TARGETS):
        y, p = arrays(target, label)
        assignments = np.minimum((p * 10).astype(int), 9)
        ax = axes[0, j]
        ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Ideal reliability")
        for k in range(10):
            selected = assignments == k
            count = int(selected.sum())
            if not count:
                continue
            mean, observed = float(p[selected].mean()), float(y[selected].mean())
            ax.scatter(mean, observed, s=30 + count * 0.35, color=COLORS[0], alpha=0.7)
            ax.annotate(
                f"n={count}",
                (mean, observed),
                xytext=(-6 if mean > 0.85 else 4, 7 + 14 * (k % 2)),
                textcoords="offset points",
                fontsize=8,
                ha="right" if mean > 0.85 else "left",
                arrowprops={"arrowstyle": "-", "linewidth": 0.4, "color": "#8293a7"},
            )
            bins_output.append(
                {
                    "target": target,
                    "left": float(edges[k]),
                    "right": float(edges[k + 1]),
                    "n": count,
                    "mean_probability": mean,
                    "observed_fraction": observed,
                }
            )
        ax.set(
            xlim=(-0.04, 1.08),
            ylim=(-0.04, 1.16),
            xlabel="Mean probability in bin",
            ylabel="Observed positive fraction",
            title=f"{target}: reliability, fixed 10 bins",
        )
        ax.legend(fontsize=8, loc="lower right")
        ax = axes[1, j]
        for value, color, hatch in ((0, "#447dba", "//"), (1, "#dc6357", "")):
            ax.hist(
                p[y == value],
                bins=edges,
                alpha=0.6,
                color=color,
                hatch=hatch,
                label=f"Observed {'yes' if value else 'no'}: n={(y == value).sum()}",
            )
        ax.set(
            xlabel="Forecast probability",
            ylabel="Test windows",
            title=f"{target}: probability distribution",
        )
        ax.legend(fontsize=9)
    save(fig, "reliability", "全测试队列校准与概率分布")
    write(output / "reliability_bins.json", bins_output)

    fig, axes = figure((11, 4.5), (1, 2))
    years = (2023, 2024, 2025)
    x = np.arange(len(years))
    for ax, (_, target, _) in zip(axes, TARGETS, strict=True):
        for model, offset, color, name in (
            ("selected_calibrated", -0.18, COLORS[0], "SHARP RF"),
            ("always_yes", 0.18, COLORS[2], "Always yes"),
        ):
            data = [
                next(
                    r
                    for r in reference["years"]
                    if r["cohort"] == "primary"
                    and r["target"] == target
                    and r["model_id"] == model
                    and int(r["year"]) == year
                )
                for year in years
            ]
            values = [float(r["Accuracy"]) for r in data]
            ax.bar(x + offset, values, width=0.34, color=color, label=name)
            for pos, value in zip(x + offset, values, strict=True):
                ax.text(pos, value + 0.02, f"{value:.1%}", ha="center", fontsize=9)
        ax.set_xticks(
            x,
            [
                f"{y}\nn={sum(r['target_start_utc'].startswith(str(y)) for r in rows)}"
                for y in years
            ],
        )
        ax.set(ylim=(0, 1.15), title=f"{target}: accuracy by test year")
        ax.legend(loc="lower left", fontsize=9)
    save(fig, "years", "2023—2025逐年表现")

    fig, axes = figure((12, 5), (1, 2))
    for ax, (prefix, target, _) in zip(axes, TARGETS, strict=True):
        data = sorted(
            reference["importance"],
            key=lambda r: float(r[f"{prefix}_Brier_increase_mean"]),
            reverse=True,
        )[:8][::-1]
        ax.barh(
            [r["feature"] for r in data],
            [float(r[f"{prefix}_Brier_increase_mean"]) for r in data],
            xerr=[float(r[f"{prefix}_Brier_increase_std"]) for r in data],
            color=COLORS[0],
            capsize=3,
        )
        ax.axvline(0, color="gray", linewidth=0.8)
        ax.set(
            title=f"{target}: frozen top 8 of 23 features",
            xlabel="Permutation Brier increase (10 repeats: mean +/- SD)",
        )
        ax.tick_params(axis="y", labelsize=9)
    save(fig, "importance", "C/M分别展示的冻结参数重要性")

    fig, axes = figure((12, 6), (2, 1))
    for ax, (_, target, label) in zip(axes, TARGETS, strict=True):
        y, p = arrays(target, label)
        line_dates, values = [], []
        for i, stamp in enumerate(dates):
            if i and (stamp - dates[i - 1]).total_seconds() > 48 * 3600:
                line_dates.append(stamp)
                values.append(float("nan"))
            line_dates.append(stamp)
            values.append(p[i])
        ax.plot(line_dates, values, linewidth=0.7, label="Frozen RF probability")
        ax.scatter(dates, y, alpha=0.16, s=5, label="Observed binary label")
        ax.axhline(0.5, linestyle="--", color="gray", linewidth=0.6)
        ax.set(ylim=(-0.04, 1.06), ylabel=target)
        ax.legend(loc="lower left", fontsize=9)
    axes[-1].set_xlabel(
        "Historical window start (UTC); gaps >48h break the curve; not a future trajectory"
    )
    save(fig, "history", "全部测试窗口历史概率曲线")
    return records
