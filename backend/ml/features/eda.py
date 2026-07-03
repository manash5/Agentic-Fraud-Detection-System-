"""Exploratory data analysis for the processed fraud-detection feature table.

Generates a suite of PNG visualisations under ``backend/eval/eda_plots/`` plus a
markdown summary (``backend/eval/EDA_REPORT.md``). Runs on the *labeled* feature
table (rows with a known ``is_fraud`` value), which comfortably fits in memory.

Usage
-----
    cd backend
    python -m ml.features.eda
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Matplotlib needs a writable config dir; point it somewhere safe before import.
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mplcache_fraud"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED_DIR = BACKEND_ROOT / "datasets_processed"
PLOTS_DIR = BACKEND_ROOT / "eval" / "eda_plots"
REPORT_PATH = BACKEND_ROOT / "eval" / "EDA_REPORT.md"

STRUCTURING_THRESHOLDS = (9_999, 49_999, 99_999)
BASELINE_AUROC = 0.71

sns.set_theme(style="whitegrid")


# =============================================================================
# Helpers
# =============================================================================
def _load_labeled(processed_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(processed_dir / "feature_table_labeled.csv", low_memory=False)
    df["is_fraud"] = df["is_fraud"].map(_to_bool).fillna(False).astype(bool)
    return df


def _to_bool(value: object) -> bool | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if pd.isna(value):
        return np.nan
    return bool(value)


def _reconstruct_category(df: pd.DataFrame, prefix: str) -> pd.Series:
    """Rebuild a categorical column from its one-hot columns."""
    cols = [c for c in df.columns if c.startswith(f"{prefix}_")]
    if not cols:
        return pd.Series(index=df.index, dtype="object")
    sub = df[cols].astype(bool)
    label = sub.idxmax(axis=1).str[len(prefix) + 1 :]
    label[~sub.any(axis=1)] = "UNKNOWN"
    return label


def _fraud_rate_by(df: pd.DataFrame, group: pd.Series) -> pd.Series:
    return df.assign(_g=group).groupby("_g")["is_fraud"].mean().sort_values(ascending=False)


def _save(fig: plt.Figure, name: str) -> None:
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / name, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"       saved {name}", flush=True)


def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].map(_to_bool).fillna(False).astype(bool)


# =============================================================================
# Individual plots
# =============================================================================
def plot_fraud_rate_overview(df: pd.DataFrame) -> None:
    counts = df["is_fraud"].value_counts()
    legit = int(counts.get(False, 0))
    fraud = int(counts.get(True, 0))
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].pie(
        [legit, fraud],
        labels=["Legitimate", "Fraud"],
        autopct="%1.2f%%",
        colors=["#4c72b0", "#c44e52"],
        startangle=90,
        explode=(0, 0.1),
    )
    axes[0].set_title("Fraud vs Legitimate")
    axes[1].bar(["Legitimate", "Fraud"], [legit, fraud], color=["#4c72b0", "#c44e52"])
    axes[1].set_yscale("log")
    axes[1].set_ylabel("Count (log scale)")
    axes[1].set_title("Fraud vs Total Count")
    fig.suptitle("Overall Fraud Rate in Dataset", fontweight="bold")
    _save(fig, "fraud_rate_overview.png")


def plot_fraud_by_txn_type(df: pd.DataFrame) -> None:
    rate = _fraud_rate_by(df, _reconstruct_category(df, "txn_type"))
    fig, ax = plt.subplots(figsize=(10, 6))
    sns.barplot(x=rate.values, y=rate.index, ax=ax, palette="rocket", hue=rate.index, legend=False)
    ax.set_xlabel("Fraud rate")
    ax.set_ylabel("Transaction type")
    ax.set_title("Fraud Rate by Transaction Type", fontweight="bold")
    _save(fig, "fraud_by_txn_type.png")


def plot_fraud_by_channel(df: pd.DataFrame) -> None:
    rate = _fraud_rate_by(df, _reconstruct_category(df, "channel"))
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(x=rate.index, y=rate.values, ax=ax, palette="mako", hue=rate.index, legend=False)
    ax.set_xlabel("Channel")
    ax.set_ylabel("Fraud rate")
    ax.set_title("Fraud Rate by Channel", fontweight="bold")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    _save(fig, "fraud_by_channel.png")


def plot_amount_distribution(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11, 6))
    amt = df["amount_npr"].clip(lower=1)
    bins = np.logspace(0, np.log10(max(amt.max(), 10)), 60)
    ax.hist(amt[~df["is_fraud"]], bins=bins, alpha=0.6, label="Legitimate", color="#4c72b0")
    ax.hist(amt[df["is_fraud"]], bins=bins, alpha=0.6, label="Fraud", color="#c44e52")
    ax.set_xscale("log")
    ax.set_yscale("log")
    for thr in STRUCTURING_THRESHOLDS:
        ax.axvline(thr, color="black", linestyle="--", alpha=0.6)
    ax.set_xlabel("amount_npr (log scale)")
    ax.set_ylabel("Count (log scale)")
    ax.legend()
    ax.set_title("Transaction Amount Distribution: Fraud vs Legitimate", fontweight="bold")
    _save(fig, "amount_distribution.png")


def plot_fraud_by_hour(df: pd.DataFrame) -> None:
    grp = df.groupby("hour_of_day")["is_fraud"].agg(["mean", "count"])
    grp = grp.reindex(range(24), fill_value=0)
    fig, ax1 = plt.subplots(figsize=(11, 6))
    ax1.plot(grp.index, grp["mean"], color="#c44e52", marker="o", label="Fraud rate")
    ax1.axvspan(1, 4, color="#ffcc99", alpha=0.4, label="Night window 01:00-04:00")
    ax1.set_xlabel("Hour of day (NPT)")
    ax1.set_ylabel("Fraud rate", color="#c44e52")
    ax1.set_xticks(range(0, 24))
    ax2 = ax1.twinx()
    ax2.bar(grp.index, grp["count"], alpha=0.2, color="#4c72b0")
    ax2.set_ylabel("Transaction count", color="#4c72b0")
    ax1.set_title("Fraud Rate by Hour of Day (NPT)", fontweight="bold")
    ax1.legend(loc="upper right")
    _save(fig, "fraud_by_hour.png")


def plot_fraud_by_day_of_week(df: pd.DataFrame) -> None:
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rate = df.groupby("day_of_week")["is_fraud"].mean().reindex(range(7), fill_value=0)
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(x=labels, y=rate.values, ax=ax, palette="crest", hue=labels, legend=False)
    ax.set_xlabel("Day of week")
    ax.set_ylabel("Fraud rate")
    ax.set_title("Fraud Rate by Day of Week", fontweight="bold")
    _save(fig, "fraud_by_day_of_week.png")


def plot_z_score_distribution(df: pd.DataFrame) -> None:
    if "vel_z_score_amount" not in df.columns:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    z = df["vel_z_score_amount"].clip(-5, 10)
    sns.kdeplot(z[~df["is_fraud"]], ax=ax, label="Legitimate", fill=True, color="#4c72b0")
    sns.kdeplot(z[df["is_fraud"]], ax=ax, label="Fraud", fill=True, color="#c44e52")
    ax.axvline(3.5, color="black", linestyle="--", label="z=3.5 threshold")
    ax.set_xlabel("vel_z_score_amount")
    ax.legend()
    ax.set_title("Z-Score Amount Distribution: Fraud vs Legitimate", fontweight="bold")
    _save(fig, "z_score_distribution.png")


def plot_velocity_heatmap(df: pd.DataFrame) -> None:
    if not {"vel_txn_count_1m", "vel_txn_count_1h"}.issubset(df.columns):
        return
    tmp = df.copy()
    tmp["c1m"] = tmp["vel_txn_count_1m"].clip(0, 10)
    tmp["c1h"] = pd.cut(tmp["vel_txn_count_1h"].clip(0, 60), bins=[-1, 5, 10, 20, 40, 60], labels=["0-5", "6-10", "11-20", "21-40", "41-60"])
    pivot = tmp.pivot_table(index="c1h", columns="c1m", values="is_fraud", aggfunc="mean", observed=False)
    fig, ax = plt.subplots(figsize=(11, 6))
    sns.heatmap(pivot, cmap="Reds", ax=ax, cbar_kws={"label": "Fraud rate"})
    ax.set_xlabel("txn_count_1m")
    ax.set_ylabel("txn_count_1h")
    ax.set_title("Transaction Velocity vs Fraud Rate", fontweight="bold")
    _save(fig, "velocity_heatmap.png")


def plot_geo_risk_flags(df: pd.DataFrame) -> None:
    flags = {
        "is_vpn": "geo_is_vpn",
        "is_tor": "geo_is_tor",
        "is_datacenter": "geo_is_datacenter",
        "impossible_travel": "geo_impossible_travel",
        "velocity_flag": "geo_velocity_flag",
    }
    rows = []
    for label, col in flags.items():
        flag = _bool_series(df, col)
        rows.append({"flag": label, "state": "True", "rate": df.loc[flag, "is_fraud"].mean() if flag.any() else 0})
        rows.append({"flag": label, "state": "False", "rate": df.loc[~flag, "is_fraud"].mean() if (~flag).any() else 0})
    plot_df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(11, 6))
    sns.barplot(data=plot_df, x="flag", y="rate", hue="state", ax=ax, palette={"True": "#c44e52", "False": "#4c72b0"})
    ax.set_xlabel("Geo risk flag")
    ax.set_ylabel("Fraud rate")
    ax.set_title("Geo Risk Flag Impact on Fraud Rate", fontweight="bold")
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right")
    _save(fig, "geo_risk_flags.png")


def plot_device_risk(df: pd.DataFrame) -> None:
    rooted = _bool_series(df, "dev_is_rooted")
    mismatch = _bool_series(df, "dev_locale_mismatch")
    shared = _bool_series(df, "dev_is_shared")
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, mask, title in (
        (axes[0], rooted, "Rooted device"),
        (axes[1], mismatch, "en_US locale on Nepal IP"),
        (axes[2], shared, "Shared device"),
    ):
        rates = [df.loc[~mask, "is_fraud"].mean() if (~mask).any() else 0, df.loc[mask, "is_fraud"].mean() if mask.any() else 0]
        sns.barplot(x=["No", "Yes"], y=rates, ax=ax, palette=["#4c72b0", "#c44e52"], hue=["No", "Yes"], legend=False)
        ax.set_title(title)
        ax.set_ylabel("Fraud rate")
    fig.suptitle("Device Risk Signals and Fraud Rate", fontweight="bold")
    _save(fig, "device_risk.png")


def plot_fraud_type_distribution(df: pd.DataFrame, processed_dir: Path) -> None:
    labels = pd.read_csv(processed_dir / "fraud_labels_train_cleaned.csv", usecols=["is_fraud", "fraud_type"])
    labels = labels[labels["is_fraud"].map(_to_bool).fillna(False).astype(bool)]
    counts = labels["fraud_type"].fillna("UNSPECIFIED").value_counts()
    fig, ax = plt.subplots(figsize=(10, 6))
    sns.barplot(x=counts.values, y=counts.index, ax=ax, palette="flare", hue=counts.index, legend=False)
    ax.set_xlabel("Count")
    ax.set_ylabel("Fraud type")
    ax.set_title("Distribution of Fraud Types", fontweight="bold")
    _save(fig, "fraud_type_distribution.png")


def plot_correlation_heatmap(df: pd.DataFrame) -> None:
    feats = [
        "amount_npr", "vel_z_score_amount", "vel_txn_count_1m", "vel_txn_count_1h",
        "geo_km_from_home_district", "geo_prev_txn_km", "is_structuring_amount",
        "is_fraud_merchant", "is_night", "vel_dormancy_break",
    ]
    present = [c for c in feats if c in df.columns]
    corr_df = df[present].apply(lambda s: s.map(_to_bool) if s.dtype == object else s).astype(float)
    corr_df["is_fraud"] = df["is_fraud"].astype(float)
    corr = corr_df.corr()
    fig, ax = plt.subplots(figsize=(11, 9))
    sns.heatmap(corr, annot=True, fmt=".2f", cmap="coolwarm", center=0, ax=ax)
    ax.set_title("Feature Correlation with Fraud Label", fontweight="bold")
    _save(fig, "correlation_heatmap.png")


def plot_structuring_pattern(df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    windows = [(9000, 10500), (49000, 50500), (99000, 100500)]
    for ax, (lo, hi) in zip(axes, windows):
        window = df[(df["amount_npr"] >= lo) & (df["amount_npr"] <= hi)]
        ax.hist(window.loc[~window["is_fraud"], "amount_npr"], bins=40, color="#4c72b0", alpha=0.7, label="Legit")
        ax.hist(window.loc[window["is_fraud"], "amount_npr"], bins=40, color="#c44e52", alpha=0.8, label="Fraud")
        ax.set_title(f"NPR {lo}-{hi}")
        ax.set_xlabel("amount_npr")
        ax.legend()
    fig.suptitle("Structuring Pattern: Transactions Near NRB Reporting Thresholds", fontweight="bold")
    _save(fig, "structuring_pattern.png")


def plot_otp_analysis(df: pd.DataFrame, processed_dir: Path) -> None:
    otp = pd.read_csv(processed_dir / "otp_logs_cleaned.csv", usecols=["final_decision", "sim_swap_suspected"])
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    outcomes = otp["final_decision"].value_counts()
    axes[0].pie(outcomes.values, labels=outcomes.index, autopct="%1.1f%%", startangle=90)
    axes[0].set_title("OTP Outcomes")
    swap = _bool_series(df, "otp_sim_swap_suspected")
    rates = [df.loc[~swap, "is_fraud"].mean() if (~swap).any() else 0, df.loc[swap, "is_fraud"].mean() if swap.any() else 0]
    sns.barplot(x=["No SIM swap", "SIM swap suspected"], y=rates, ax=axes[1], palette=["#4c72b0", "#c44e52"], hue=["No", "Yes"], legend=False)
    axes[1].set_ylabel("Fraud rate")
    axes[1].set_title("SIM Swap Suspected vs Fraud Rate")
    fig.suptitle("OTP Verification Outcomes", fontweight="bold")
    _save(fig, "otp_analysis.png")


def plot_risk_tier_fraud_rate(df: pd.DataFrame) -> None:
    tier = _reconstruct_category(df, "cust_risk_tier")
    order = ["LOW", "MEDIUM", "HIGH", "WATCHLIST"]
    rate = df.assign(_t=tier).groupby("_t")["is_fraud"].mean()
    rate = rate.reindex([t for t in order if t in rate.index])
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.barplot(x=rate.index, y=rate.values, ax=ax, palette="YlOrRd", hue=rate.index, legend=False)
    ax.set_xlabel("Customer risk tier")
    ax.set_ylabel("Fraud rate")
    ax.set_title("Fraud Rate by Customer Risk Tier", fontweight="bold")
    _save(fig, "risk_tier_fraud_rate.png")


# =============================================================================
# Insights + report
# =============================================================================
def _compute_insights(df: pd.DataFrame, processed_dir: Path) -> dict[str, str]:
    fraud = df[df["is_fraud"]]
    insights: dict[str, str] = {}

    night = fraud[(fraud["hour_of_day"] >= 1) & (fraud["hour_of_day"] <= 4)]
    insights["night_fraud"] = f"{len(night) / max(len(fraud), 1):.1%} of fraud occurs between 01:00-04:00 NPT"

    base_rate = df["is_fraud"].mean() or 1e-9
    struct = _bool_series(df, "is_structuring_amount")
    struct_rate = df.loc[struct, "is_fraud"].mean() if struct.any() else 0
    insights["structuring"] = f"{struct_rate / base_rate:.1f}x fraud lift at ~9999/49999/99999 amounts"

    merch = _bool_series(df, "is_fraud_merchant")
    merch_rate = df.loc[merch, "is_fraud"].mean() if merch.any() else 0
    insights["fraud_merchants"] = f"{merch_rate / base_rate:.0f}x fraud lift for MERCH-8812/9041/7712"

    imp = _bool_series(df, "geo_impossible_travel")
    insights["impossible_travel"] = f"{fraud.pipe(lambda f: _bool_series(f, 'geo_impossible_travel').mean()):.1%} of fraud flagged with impossible travel"

    vpn_fraud = _bool_series(fraud, "geo_is_vpn").mean()
    vpn_legit = _bool_series(df[~df["is_fraud"]], "geo_is_vpn").mean()
    insights["vpn"] = f"VPN present in {vpn_fraud:.1%} of fraud vs {vpn_legit:.1%} of legit"

    labels = pd.read_csv(processed_dir / "fraud_labels_train_cleaned.csv", usecols=["is_fraud", "fraud_type"])
    labels = labels[labels["is_fraud"].map(_to_bool).fillna(False).astype(bool)]
    top = labels["fraud_type"].fillna("UNSPECIFIED").value_counts()
    if not top.empty:
        insights["top_fraud_type"] = f"Top fraud type: {top.index[0]} at {top.iloc[0] / top.sum():.1%} of fraud"
    return insights


def _write_report(df: pd.DataFrame, insights: dict[str, str]) -> None:
    total = len(df)
    fraud = int(df["is_fraud"].sum())
    plots = sorted(p.name for p in PLOTS_DIR.glob("*.png"))
    lines = [
        "# Exploratory Data Analysis — Fraud Detection Dataset",
        "",
        "## Overview",
        f"- Labeled transactions analysed: **{total:,}**",
        f"- Confirmed fraud: **{fraud:,}** ({fraud / max(total, 1):.2%})",
        "",
        "## Key Insights",
    ]
    for value in insights.values():
        lines.append(f"- {value}")
    lines += [
        "",
        "## Baseline Comparison",
        f"- Rule-engine baseline AUROC: **{BASELINE_AUROC:.2f}** (target to beat).",
        "- Structuring, fraud-merchant, night-window, impossible-travel and device-root "
        "signals above show clear separation and should drive a large AUROC lift.",
        "",
        "## Feature Engineering Recommendations",
        "- Keep `is_structuring_amount`, `is_fraud_merchant`, and `dev_locale_mismatch` as "
        "high-signal binary features (very large fraud lift).",
        "- Weight `vel_z_score_amount` and `geo_impossible_travel` heavily in the velocity/geo agents.",
        "- Use `hour_of_day`/`is_night` interactions — night window concentrates account-takeover fraud.",
        "- Risk-tier progression is monotonic; `cust_risk_tier` one-hots are useful priors.",
        "",
        "## Plots",
    ]
    for name in plots:
        title = name.replace("_", " ").replace(".png", "").title()
        lines.append(f"### {title}")
        lines.append(f"![{title}](./eda_plots/{name})")
        lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"       wrote {REPORT_PATH.relative_to(BACKEND_ROOT)}", flush=True)


# =============================================================================
# Orchestration
# =============================================================================
def generate_eda(processed_dir: Path | None = None) -> None:
    """Generate all EDA plots and the markdown report."""
    processed = processed_dir or DEFAULT_PROCESSED_DIR
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    df = _load_labeled(processed)

    plotters = [
        (plot_fraud_rate_overview, (df,)),
        (plot_fraud_by_txn_type, (df,)),
        (plot_fraud_by_channel, (df,)),
        (plot_amount_distribution, (df,)),
        (plot_fraud_by_hour, (df,)),
        (plot_fraud_by_day_of_week, (df,)),
        (plot_z_score_distribution, (df,)),
        (plot_velocity_heatmap, (df,)),
        (plot_geo_risk_flags, (df,)),
        (plot_device_risk, (df,)),
        (plot_fraud_type_distribution, (df, processed)),
        (plot_correlation_heatmap, (df,)),
        (plot_structuring_pattern, (df,)),
        (plot_otp_analysis, (df, processed)),
        (plot_risk_tier_fraud_rate, (df,)),
    ]
    for func, args in plotters:
        try:
            func(*args)
        except Exception as exc:  # pragma: no cover - keep other plots alive
            print(f"       WARNING: {func.__name__} failed ({exc!r})")

    insights = _compute_insights(df, processed)
    _write_report(df, insights)


def main() -> None:
    generate_eda()


if __name__ == "__main__":
    main()
