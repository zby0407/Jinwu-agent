"""Deterministic figures from computed C tables, with no invented observations."""

from pathlib import Path
from functools import wraps


def _plot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _isolated_style(function):
    """A caller's matplotlib style must not change reproducible artifacts."""

    @wraps(function)
    def render(*args, **kwargs):
        plt = _plot()
        with plt.rc_context(plt.rcParamsDefault):
            return function(*args, **kwargs)

    return render


@_isolated_style
def render_c1(output, tables):
    plt = _plot()
    annual = tables["annual_matched.csv"]
    primary = [
        r
        for r in tables["cycle_annual_metrics.csv"]
        if r["scenario"] == "calendar_days__whole_calendar_year"
    ]
    reference = {r["dataset"]: r["reference_cycle_mean"] for r in primary}
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), layout="constrained")
    for product, key, color in [
        ("SILSO", "silso_calendar_days", "#315d7d"),
        ("GNbb2", "gnbb2", "#ac592f"),
    ]:
        axes[0].plot(
            [r["year"] for r in annual],
            [r[key] / reference[product] for r in annual],
            color=color,
            label=product,
            lw=1,
        )
        rows = [r for r in primary if r["dataset"] == product]
        axes[1].plot(
            [r["cycle"] for r in rows],
            [r["scaled_annual_mean"] for r in rows],
            "o-",
            color=color,
            label=product,
        )
    axes[0].set(
        xlabel="Calendar year",
        ylabel="Annual value / own reference mean",
        title="Matched annual products (not independent observations)",
    )
    axes[1].set(
        xlabel="Complete cycle",
        ylabel="Cycle annual mean / own reference mean",
        xticks=list(range(1, 24)),
    )
    for ax in axes:
        ax.grid(alpha=0.2)
        ax.legend()
    fig.savefig(
        Path(output) / "C1_annual_comparison.png",
        dpi=150,
        metadata={"Software": "JW C1 table renderer"},
    )
    plt.close(fig)


@_isolated_style
def render_c2(output, tables):
    plt = _plot()
    rows = tables["scenario_targets.csv"]
    names = list(dict.fromkeys(r["scenario"] for r in rows))
    fig, ax = plt.subplots(figsize=(10, max(4, len(names) * 0.4)), layout="constrained")
    for j, target in enumerate([3, 4, 5, 6]):
        selected = [r for r in rows if r["cycle"] == target]
        ax.scatter(
            [r["margin_above_background"] for r in selected],
            [names.index(r["scenario"]) + (j - 1.5) * 0.14 for r in selected],
            label=f"SC{target}",
            s=24,
        )
    ax.axvline(0, color="black", lw=1)
    ax.set(
        yticks=range(len(names)),
        yticklabels=names,
        xlabel="Target RMS minus maximum LOO background (not a confidence bound)",
        title="C2 sensitivity: all frozen scenarios in this batch",
    )
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.2)
    ax.legend(ncol=4, loc="lower right")
    fig.savefig(
        Path(output) / "C2_sensitivity_margins.png",
        dpi=150,
        metadata={"Software": "JW C2 table renderer"},
    )
    plt.close(fig)
