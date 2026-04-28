"""S.T.A.R.K. — Smart Training & Athletic Readiness Kernel"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

from src.config.logging_config import setup_logging
setup_logging()

import logfire

logfire.configure()
logfire.instrument_pydantic_ai()

import datetime
import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.db.connection import (
    StarkDatabase,
    load_athlete_config,
    load_daily_inputs,
    load_race_predictions,
    save_athlete_config,
    save_daily_input,
)
from src.db.transformations import (
    process_fit_files,
    process_health_telemetry_jsons,
    process_hydration_jsons,
    process_sleep_jsons,
    process_weight_jsons,
)
from src.extractors.garmin import run_full_extraction

logger = logging.getLogger("main")

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="S.T.A.R.K.",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Metric tooltips ────────────────────────────────────────────────────────────

_TOOLTIPS = {
    "daily_readiness": (
        "**Daily Readiness**\n\n"
        "Snapshot of your recovery state this morning. Each metric is shown "
        "alongside its delta vs your personal 7-day average — green arrows mean "
        "you're above your baseline, red means below.\n\n"
        "- **HRV** — Heart Rate Variability (ms). Higher = more recovered. "
        "The single best proxy for autonomic nervous system readiness.\n"
        "- **Body Battery** — Garmin's composite energy reserve (0-100). "
        "Charged by sleep, drained by stress and exercise.\n"
        "- **Resting HR** — Lower than your average = good recovery. "
        "Elevated RHR often precedes illness or overtraining.\n"
        "- **Sleep Score** — Garmin's 0-100 quality score combining duration, "
        "stages, and disturbances.\n"
        "- **Soreness** — Your manual input (1-10). Feeds into session "
        "recommendations; ≥7 triggers an alert."
    ),
    "load_balance": (
        "**Load Balance — ATL / CTL / TSB**\n\n"
        "The most important chart for injury prevention. Compares your recent "
        "fatigue against your accumulated fitness.\n\n"
        "- **CTL** *(Chronic Training Load — blue)* — 42-day rolling average of "
        "daily TSS. Represents your current fitness level. Grows slowly.\n"
        "- **ATL** *(Acute Training Load — red)* — 7-day exponential moving average. "
        "Represents fatigue. Spikes fast after hard weeks.\n"
        "- **TSB** *(Training Stress Balance = CTL − ATL)* — your 'form'.\n"
        "  - TSB > +10 → Fresh / Race-ready\n"
        "  - TSB 0 to +10 → Neutral, maintain\n"
        "  - TSB −10 to 0 → Productive overreach, building fitness\n"
        "  - TSB −25 to −10 → High fatigue, protect recovery\n"
        "  - TSB < −25 → Overtraining risk, mandatory rest\n\n"
        "**TSS** is calculated using running power (rTSS) when available, "
        "or heart rate vs your LTHR (hrTSS) as fallback."
    ),
    "intensity_distribution": None,  # built dynamically by _intensity_tooltip(lthr)
    "run_efficiency": (
        "**Run Efficiency — Aerobic Adaptation Signal**\n\n"
        "Measures whether your 'Arc Reactor' is becoming more efficient over time. "
        "Only uses runs where average HR is below your Z3 threshold (easy effort), "
        "isolating the aerobic signal from tempo/race efforts.\n\n"
        "- **Orange line (Pace)** — Average pace on easy runs, in min/km. "
        "Y-axis is **inverted**: faster pace appears higher on the chart.\n"
        "- **Purple line (HR)** — Average heart rate on those same runs.\n\n"
        "**What to look for:** Over weeks and months, the orange line should "
        "rise (getting faster) while the purple line stays flat or drops "
        "(same or lower HR). That crossover is aerobic adaptation.\n\n"
        "If both lines rise together, you are running easy days too hard."
    ),
    "hrv_battery": (
        "**HRV & Body Battery — 30-day Trend**\n\n"
        "- **HRV (purple)** — Night-time Heart Rate Variability average. "
        "The trend matters more than individual values. A declining HRV trend "
        "over 5–7 days is an early warning of overtraining or illness.\n"
        "- **Body Battery (cyan area)** — Garmin's daily energy reserve. "
        "Healthy pattern: charges to 80-100 overnight, depletes through the day. "
        "If it stops reaching 70+ even after sleep, recovery is compromised."
    ),
    "resting_hr": (
        "**Resting Heart Rate — 30-day Trend**\n\n"
        "Measured by Garmin during sleep. A downward trend over weeks = improving "
        "cardiovascular fitness. An upward spike of ≥5 bpm above your baseline "
        "is a reliable signal of accumulated fatigue, illness onset, or "
        "insufficient recovery. Lower is better."
    ),
    "sleep_score": (
        "**Sleep Score — 30-day Trend**\n\n"
        "Garmin's composite score (0–100) combining total duration, sleep stages "
        "(deep, REM, light), and disturbances.\n\n"
        "- **Green (EXCELLENT / GOOD)** — 70+. Full recovery signal.\n"
        "- **Yellow (FAIR)** — 50–69. Partial recovery; consider adjusting load.\n"
        "- **Red (POOR)** — <50. Prioritise sleep over training intensity.\n\n"
        "The dotted line marks the 70-point 'good' threshold."
    ),
    "weight": (
        "**Weight — 30-day Trend**\n\n"
        "Body weight logged in Garmin Connect. The dotted line is a linear trend.\n\n"
        "For half marathon performance, gradual weight reduction improves running economy "
        "(roughly 1% faster per kg lost). The trend matters more than daily fluctuations, "
        "which can vary ±1 kg from hydration alone."
    ),
    "hydration": (
        "**Hydration — 30-day Trend**\n\n"
        "Daily water intake logged in Garmin Connect vs your personalised goal.\n\n"
        "- **Blue bars** — Total intake in ml. Color shifts green when you hit ≥80% of goal.\n"
        "- **Cyan line** — Daily goal (ml), adjusted by Garmin based on activity and sweat loss.\n"
        "- **Orange markers** — Sweat loss estimated by Garmin on run days.\n\n"
        "Chronic under-hydration elevates resting HR, compresses HRV, and impairs "
        "recovery. On run days, sweat loss directly sets the minimum intake needed "
        "just to break even — you need to exceed it to actually rehydrate."
    ),
}


def _intensity_tooltip(lthr: int = 0, zone_source: str = "friel_fallback") -> str:
    from src.db.connection import _zone_thresholds
    if zone_source == "garmin":
        src = "Garmin's own zone definitions (matches your watch and Garmin Connect)"
        zone_lines = (
            "- **Z1** — Very easy / recovery\n"
            "- **Z2** — Aerobic base, fat oxidation zone\n"
            "- **Z3** — Tempo / threshold approach\n"
            "- **Z4** — Lactate threshold work\n"
            "- **Z5** — VO₂max / sprint intervals\n"
        )
    else:
        z1, z2, z3, z4 = _zone_thresholds(lthr or None)
        src = f"Friel zones based on your LTHR of {lthr} bpm" if lthr else "default absolute thresholds (set LTHR in Athlete Config for personalised zones)"
        zone_lines = (
            f"- **Z1 (<{z1} bpm)** — Recovery / very easy\n"
            f"- **Z2 ({z1}–{z2 - 1} bpm)** — Aerobic base, fat oxidation zone\n"
            f"- **Z3 ({z2}–{z3 - 1} bpm)** — Tempo / threshold approach\n"
            f"- **Z4 ({z3}–{z4 - 1} bpm)** — Lactate threshold work\n"
            f"- **Z5 (≥{z4} bpm)** — VO₂max / sprint intervals\n"
        )
    return (
        "**Intensity Distribution — 80/20 Rule**\n\n"
        "Validates your training strategy. Elite endurance athletes spend ~80% "
        "of their training time at low intensity (Z1+Z2) and only ~20% hard.\n\n"
        "Amateur runners often fail at half marathon because they run their "
        "easy days too fast, accumulating hidden fatigue.\n\n"
        f"Zone thresholds: {src}.\n\n"
        f"{zone_lines}\n"
        "The white dotted line marks the 80% target. "
        "Ideally, the green+light-green bar reaches that line."
    )


def _section_header(title: str, tooltip_key: str, tooltip_text: str = "") -> None:
    """Renders a section title with an inline info popover."""
    col_title, col_info = st.columns([10, 1])
    col_title.subheader(title)
    with col_info:
        with st.popover("ℹ️", use_container_width=False):
            st.markdown(tooltip_text or _TOOLTIPS[tooltip_key])


# ── Cached data ────────────────────────────────────────────────────────────────

@st.cache_resource
def get_db() -> StarkDatabase:
    return StarkDatabase()


@st.cache_data(ttl=300, show_spinner=False)
def load_runs(limit: int = 20) -> pd.DataFrame:
    rows = get_db().get_recent_runs(limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["distance_km"] = (df["total_distance_meters"] / 1000).round(2)
    df["pace_sec_km"] = df["avg_speed_m_s"].apply(
        lambda s: round(1000 / s, 1) if s and s > 0 else None
    )
    return df.sort_values("run_date")


@st.cache_data(ttl=300, show_spinner=False)
def load_health(days: int = 30) -> pd.DataFrame:
    rows = get_db().get_health_trend(days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_weekly_intensity(weeks: int = 2, lthr: int = 0) -> pd.DataFrame:
    rows = get_db().get_weekly_intensity(weeks, lthr=lthr or None)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_efficiency_trend(weeks: int = 16, lthr: int = 0) -> pd.DataFrame:
    rows = get_db().get_efficiency_trend(weeks, lthr=lthr or None)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["week_start"] = pd.to_datetime(df["week_start"])
    df["avg_pace_sec_km"] = df["avg_pace_sec_km"].round(1)
    df["avg_hr"] = df["avg_hr"].round(1)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_weight(days: int = 30) -> pd.DataFrame:
    try:
        result = get_db().conn.execute(f"""
            SELECT CAST(date AS DATE) AS date, weight_kg
            FROM gold_weight
            WHERE CAST(date AS DATE) >= CAST('{(datetime.date.today() - datetime.timedelta(days=days)).isoformat()}' AS DATE)
              AND weight_kg IS NOT NULL
            ORDER BY date ASC
        """).df()
        if result.empty:
            return pd.DataFrame()
        result["date"] = pd.to_datetime(result["date"])
        return result
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def load_hydration(days: int = 30) -> pd.DataFrame:
    rows = get_db().get_hydration_trend(days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=300, show_spinner=False)
def get_race_predictions() -> dict:
    return load_race_predictions()


@st.cache_data(ttl=300, show_spinner=False)
def load_training_load(lthr: int) -> pd.DataFrame:
    rows = get_db().get_training_load_history(days=42)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["run_date"] = pd.to_datetime(df["run_date"])

    def _tss(row) -> float:
        duration = row["duration_sec"] or 0
        if duration <= 0:
            return 0.0
        avg_power = row.get("avg_power_w")
        ftp = row.get("ftp_proxy_w")
        if avg_power and avg_power > 0 and ftp and ftp > 0:
            intensity_factor = avg_power / ftp
            return round((duration * avg_power * intensity_factor) / (ftp * 3600) * 100, 1)
        if lthr and lthr > 0:
            hr_num = row.get("hr_tss_numerator") or 0
            return round(hr_num / (lthr ** 2 * 3600) * 100, 1)
        return 0.0

    df["tss"] = df.apply(_tss, axis=1)

    full_range = pd.date_range(
        start=df["run_date"].min(),
        end=pd.Timestamp.today().normalize(),
        freq="D",
    )
    spine = pd.DataFrame({"date": full_range})
    daily = df.groupby("run_date")["tss"].sum().reset_index()
    daily.columns = ["date", "tss"]
    merged = spine.merge(daily, on="date", how="left").fillna(0)

    merged["atl"] = merged["tss"].ewm(span=7, adjust=False).mean().round(1)
    merged["ctl"] = merged["tss"].rolling(42, min_periods=1).mean().round(1)
    merged["tsb"] = (merged["ctl"] - merged["atl"]).round(1)

    return merged


# ── Section 1: Daily Readiness sidebar ────────────────────────────────────────

def render_sidebar(health_df: pd.DataFrame) -> None:
    st.sidebar.title("S.T.A.R.K.")
    st.sidebar.caption("Smart Training & Athletic Readiness Kernel")
    st.sidebar.divider()

    # Section header with popover
    col_title, col_info = st.sidebar.columns([6, 1])
    col_title.subheader("Daily Readiness")
    with col_info:
        with st.popover("ℹ️"):
            st.markdown(_TOOLTIPS["daily_readiness"])

    today_str = datetime.date.today().isoformat()
    daily_inputs = load_daily_inputs()
    saved_soreness = daily_inputs.get(today_str, {}).get("soreness", 1)

    if not health_df.empty:
        latest = health_df.iloc[-1]
        week_avg = health_df.tail(7).mean(numeric_only=True)

        def safe_int(val):
            return int(val) if pd.notna(val) else None

        def safe_delta(today_val, avg_val):
            if today_val is not None and pd.notna(avg_val):
                return round(today_val - avg_val, 1)
            return None

        hrv_today = safe_int(latest["hrv_last_night_avg"])
        hrv_delta = safe_delta(hrv_today, week_avg["hrv_last_night_avg"])

        rhr_today = safe_int(latest["resting_heart_rate"])
        rhr_delta = safe_delta(rhr_today, week_avg["resting_heart_rate"])

        bb_today = safe_int(latest["body_battery_end"])
        bb_delta = safe_delta(bb_today, week_avg["body_battery_end"])

        sleep_today = safe_int(latest["sleep_score"])
        sleep_delta = safe_delta(sleep_today, week_avg["sleep_score"])

        col1, col2 = st.sidebar.columns(2)
        col1.metric(
            "HRV",
            f"{hrv_today} ms" if hrv_today else "—",
            delta=f"{hrv_delta:+.0f} vs 7d avg" if hrv_delta is not None else None,
            help=f"7-day avg: {week_avg['hrv_last_night_avg']:.0f} ms",
        )
        col2.metric(
            "Body Battery",
            str(bb_today) if bb_today else "—",
            delta=f"{bb_delta:+.0f} vs 7d avg" if bb_delta is not None else None,
            help=f"7-day avg: {week_avg['body_battery_end']:.0f}",
        )
        col3, col4 = st.sidebar.columns(2)
        col3.metric(
            "Resting HR",
            f"{rhr_today} bpm" if rhr_today else "—",
            delta=f"{rhr_delta:+.0f} vs 7d avg" if rhr_delta is not None else None,
            delta_color="inverse",
            help=f"7-day avg: {week_avg['resting_heart_rate']:.0f} bpm",
        )
        col4.metric(
            "Sleep",
            f"{sleep_today}/100" if sleep_today else "—",
            delta=f"{sleep_delta:+.0f} vs 7d avg" if sleep_delta is not None else None,
            help=f"7-day avg: {week_avg['sleep_score']:.0f}",
        )

        hrv_status = latest.get("hrv_status", "")
        if hrv_status:
            status_color = {
                "BALANCED": "green",
                "UNBALANCED": "orange",
                "LOW": "red",
                "POOR": "red",
            }.get(str(hrv_status).upper(), "gray")
            st.sidebar.markdown(f"HRV Status: :{status_color}[**{hrv_status}**]")

        st.sidebar.caption(f"Latest: {latest['date'].strftime('%b %d, %Y')}")
    else:
        st.sidebar.info("No readiness data. Sync first.")

    st.sidebar.divider()

    soreness = st.sidebar.slider(
        "Soreness today",
        min_value=1, max_value=10,
        value=saved_soreness,
        help="1 = no soreness, 10 = severe pain",
    )
    if soreness != saved_soreness:
        save_daily_input(today_str, soreness)

    if soreness >= 7:
        st.sidebar.error(f"High soreness ({soreness}/10) — consider rest or easy session.")
    elif soreness >= 5:
        st.sidebar.warning(f"Moderate soreness ({soreness}/10) — monitor load today.")

    st.sidebar.divider()
    st.sidebar.subheader("Data Pipeline")
    if st.sidebar.button(
        "Sync Data",
        use_container_width=True,
        help="Extracts new data from Garmin, then processes Bronze → Silver.\n\n"
             "Requires tokens at ~/.garminconnect.\n"
             "If missing, run: `uv run python scripts/garmin_auth.py`",
    ):
        with st.sidebar.status("Syncing...", expanded=True) as status:
            st.write("Connecting to Garmin...")
            try:
                run_full_extraction()
                st.write("Garmin extraction complete.")
            except RuntimeError as e:
                st.error(str(e))
                st.write("Skipped extraction — processing existing raw files.")
            st.write("Processing sleep data...")
            process_sleep_jsons()
            st.write("Processing health telemetry...")
            process_health_telemetry_jsons()
            st.write("Processing hydration data...")
            process_hydration_jsons()
            st.write("Processing .FIT files...")
            process_fit_files()
            st.write("Processing weight data...")
            process_weight_jsons()
            status.update(label="Sync complete!", state="complete")
        get_db().refresh_views()
        st.cache_data.clear()
        st.rerun()


# ── Section 2: Load Balance (ATL / CTL / TSB) ─────────────────────────────────

def chart_load_balance(df: pd.DataFrame) -> go.Figure:
    view = df.tail(30).copy()
    fig = go.Figure()

    tsb_pos = view["tsb"].clip(lower=0)
    tsb_neg = view["tsb"].clip(upper=0)

    fig.add_trace(go.Scatter(
        x=view["date"], y=tsb_pos,
        fill="tozeroy", fillcolor="rgba(44,160,44,0.20)",
        line=dict(color="rgba(44,160,44,0)", width=0),
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=view["date"], y=tsb_neg,
        fill="tozeroy", fillcolor="rgba(214,39,40,0.20)",
        line=dict(color="rgba(214,39,40,0)", width=0),
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="gray", line_width=1)
    fig.add_trace(go.Scatter(
        x=view["date"], y=view["ctl"],
        name="CTL — Fitness", mode="lines",
        line=dict(color="#1f77b4", width=2.5),
        hovertemplate="CTL: %{y:.1f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=view["date"], y=view["atl"],
        name="ATL — Fatigue", mode="lines",
        line=dict(color="#d62728", width=2.5),
        hovertemplate="ATL: %{y:.1f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=view["date"], y=view["tsb"],
        name="TSB — Form", mode="lines",
        line=dict(color="#aec7e8", width=1.5, dash="dot"),
        hovertemplate="TSB: %{y:.1f}<extra></extra>",
    ))
    fig.update_layout(
        title="Load Balance — ATL / CTL / TSB",
        xaxis=dict(tickformat="%b %d"),
        yaxis=dict(title="Load (AU)", zeroline=False),
        legend=dict(orientation="h", y=1.15, x=0),
        margin=dict(t=55, b=30, l=50, r=20),
        height=300,
    )
    return fig


def _load_balance_summary(df: pd.DataFrame) -> None:
    if df.empty:
        return
    latest = df.iloc[-1]
    tsb, ctl, atl = latest["tsb"], latest["ctl"], latest["atl"]
    if tsb > 10:
        state, color, advice = "Fresh / Race-ready", "green", "Good window for a key workout or race."
    elif tsb > 0:
        state, color, advice = "Neutral / Maintaining", "green", "Balanced load — sustain current training."
    elif tsb > -10:
        state, color, advice = "Productive Overreach", "orange", "Accumulating fitness — monitor recovery."
    elif tsb > -25:
        state, color, advice = "High Fatigue", "orange", "Significant fatigue — protect sleep and easy days."
    else:
        state, color, advice = "Overtraining Risk", "red", "TSB critically low — mandatory easy/rest days."
    st.markdown(
        f"TSB **{tsb:+.1f}** · CTL {ctl:.1f} · ATL {atl:.1f} — "
        f":{color}[**{state}**] — {advice}"
    )


# ── Section 3: Intensity Distribution (80/20) ─────────────────────────────────

def chart_intensity_distribution(intensity_df: pd.DataFrame, lthr: int = 0) -> go.Figure:
    from src.db.connection import _zone_thresholds

    zone_source = intensity_df["source"].iloc[0] if "source" in intensity_df.columns else "friel_fallback"

    if zone_source == "garmin":
        zone_labels = ["Z1", "Z2", "Z3", "Z4", "Z5"]
        source_label = "Garmin zones"
    else:
        z1, z2, z3, z4 = _zone_thresholds(lthr or None)
        zone_labels = [
            f"Z1 (<{z1})",
            f"Z2 ({z1}–{z2 - 1})",
            f"Z3 ({z2}–{z3 - 1})",
            f"Z4 ({z3}–{z4 - 1})",
            f"Z5 (≥{z4})",
        ]
        source_label = f"Friel / LTHR {lthr} bpm" if lthr else "Default thresholds"

    zone_colors = ["#2ca02c", "#98df8a", "#ffbb78", "#ff7f0e", "#d62728"]
    zone_cols = ["z1_min", "z2_min", "z3_min", "z4_min", "z5_min"]

    fig = go.Figure()
    week_labels = intensity_df["week_start"].dt.strftime("Week of %b %d")

    for col, label, color in zip(zone_cols, zone_labels, zone_colors):
        pct = (intensity_df[col] / intensity_df["total_min"].replace(0, float("nan")) * 100).round(1)
        fig.add_trace(go.Bar(
            name=label, y=week_labels, x=pct, orientation="h",
            marker_color=color,
            hovertemplate=f"<b>{label}</b><br>%{{x:.1f}}% (%{{customdata:.0f}} min)<extra></extra>",
            customdata=intensity_df[col],
        ))
    fig.add_vline(
        x=80, line_dash="dot", line_color="white", line_width=2,
        annotation_text="80%", annotation_font_color="white", annotation_position="top",
    )
    fig.update_layout(
        title=f"Intensity Distribution (80/20) — {source_label}",
        barmode="stack",
        xaxis=dict(title="% of Total Time", range=[0, 100], ticksuffix="%"),
        yaxis=dict(autorange="reversed"),
        legend=dict(orientation="h", y=-0.3, x=0),
        margin=dict(t=50, b=90, l=10, r=20),
        height=260,
    )
    return fig


# ── Section 4: Run Efficiency ─────────────────────────────────────────────────

def chart_run_efficiency(df: pd.DataFrame) -> go.Figure:
    pace = df["avg_pace_sec_km"]
    tick_vals, tick_texts = [], []
    if not pace.empty:
        lo = max(0, int(pace.min()) - 30)
        hi = int(pace.max()) + 60
        tick_vals = list(range((lo // 30) * 30, ((hi // 30) + 2) * 30, 30))
        tick_texts = [f"{v // 60}:{v % 60:02d}" for v in tick_vals]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["week_start"], y=df["avg_pace_sec_km"],
        name="Avg Pace (easy runs)", mode="lines+markers",
        line=dict(color="#ff7f0e", width=2.5), marker=dict(size=7),
        yaxis="y1",
        hovertemplate=[
            f"{int(v) // 60}:{int(v) % 60:02d} min/km<extra></extra>"
            for v in df["avg_pace_sec_km"]
        ],
    ))
    fig.add_trace(go.Scatter(
        x=df["week_start"], y=df["avg_hr"],
        name="Avg HR (easy runs)", mode="lines+markers",
        line=dict(color="#9467bd", width=2.5), marker=dict(size=7),
        yaxis="y2",
        hovertemplate="%{y:.0f} bpm<extra></extra>",
    ))
    hr_mean = df["avg_hr"].mean()
    fig.add_hline(
        y=hr_mean, yref="y2",
        line_dash="dot", line_color="rgba(148,103,189,0.4)", line_width=1,
        annotation_text=f"HR mean {hr_mean:.0f} bpm",
        annotation_font_color="rgba(148,103,189,0.8)",
        annotation_position="top left",
    )
    fig.update_layout(
        title="Run Efficiency — Easy Runs Only (Z1+Z2 ≥ 70%)",
        xaxis=dict(tickformat="%b %d", title="Week"),
        yaxis=dict(
            title="Pace (min/km)", autorange="reversed",
            tickvals=tick_vals, ticktext=tick_texts, showgrid=True,
        ),
        yaxis2=dict(title="Heart Rate (bpm)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.15, x=0),
        margin=dict(t=55, b=30, l=60, r=60),
        height=300,
    )
    return fig


def _efficiency_summary(df: pd.DataFrame) -> None:
    if len(df) < 4:
        return
    early = df.head(min(4, len(df) // 2))
    recent = df.tail(min(4, len(df) // 2))
    pace_delta = recent["avg_pace_sec_km"].mean() - early["avg_pace_sec_km"].mean()
    hr_delta = recent["avg_hr"].mean() - early["avg_hr"].mean()
    pace_sec = abs(int(pace_delta))
    pace_dir = "faster" if pace_delta < 0 else "slower"
    pace_color = "green" if pace_delta < 0 else "red"
    hr_dir = "lower" if hr_delta < 0 else "higher"
    hr_color = "green" if hr_delta < 0 else "orange"
    st.markdown(
        f"Trend vs earlier period: "
        f":{pace_color}[**{pace_sec}s/km {pace_dir}**] · "
        f":{hr_color}[**{abs(hr_delta):.1f} bpm {hr_dir}**]"
    )


# ── Athlete Config expander ────────────────────────────────────────────────────

def render_athlete_config_expander() -> dict:
    config = load_athlete_config()

    with st.expander("Athlete Config", icon="⚙️"):
        st.caption("Values are saved locally to data/athlete_config.json")
        c1, c2 = st.columns(2)

        target_race_date = c1.date_input(
            "Target race date",
            value=datetime.date.fromisoformat(config["target_race_date"])
                  if config.get("target_race_date") else None,
        )
        target_pace = c2.number_input(
            "Target pace (decimal min/km)",
            min_value=3.0, max_value=10.0,
            value=float(config.get("target_pace_min_per_km", 5.0)),
            step=0.083, format="%.3f",
            help="e.g. 4.917 = 4:55 min/km. One second = 0.0167.",
        )
        lthr_current = config.get("lthr")
        if lthr_current:
            c1.metric("LTHR (bpm)", lthr_current, help="Synced automatically from Garmin on each extraction.")

        st.markdown("**Shoes** — one per line: `Name | YYYY-MM-DD | max_km`")
        shoes_raw = config.get("shoes", [])
        default_shoes_text = "\n".join(
            f"{s['name']} | {s['start_date']} | {s.get('max_km', 600)}"
            for s in shoes_raw
        )
        shoes_text = st.text_area(
            "Shoes list", value=default_shoes_text, height=100,
            label_visibility="collapsed",
        )

        if st.button("Save config", type="primary"):
            shoes_parsed = []
            for line in shoes_text.strip().splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2:
                    shoes_parsed.append({
                        "name": parts[0],
                        "start_date": parts[1],
                        "max_km": int(parts[2]) if len(parts) > 2 else 600,
                    })
            new_config = {
                "target_race_date": target_race_date.isoformat() if target_race_date else None,
                "target_pace_min_per_km": round(target_pace, 3),
                "shoes": shoes_parsed,
            }
            # Preserve LTHR written by the extractor — never overwrite from the UI
            if config.get("lthr"):
                new_config["lthr"] = config["lthr"]
            save_athlete_config(new_config)
            st.success("Config saved.")
            st.cache_data.clear()
            return new_config

    return config


# ── Historical charts ─────────────────────────────────────────────────────────



def chart_hrv_body_battery(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["hrv_last_night_avg"],
        name="HRV (ms)", mode="lines+markers",
        line=dict(color="#9467bd", width=2), marker=dict(size=5), yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["body_battery_end"],
        name="Body Battery", mode="lines",
        line=dict(color="#17becf", width=2),
        fill="tozeroy", fillcolor="rgba(23,190,207,0.08)", yaxis="y2",
    ))
    fig.update_layout(
        title="HRV & Body Battery",
        xaxis=dict(tickformat="%b %d"),
        yaxis=dict(title="HRV (ms)", showgrid=False),
        yaxis2=dict(title="Body Battery", overlaying="y", side="right", range=[0, 100]),
        legend=dict(orientation="h", y=1.12),
        margin=dict(t=50, b=30, l=50, r=60),
        height=300,
    )
    return fig


def chart_resting_hr(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["resting_heart_rate"],
        name="Resting HR", mode="lines+markers",
        line=dict(color="#d62728", width=2), marker=dict(size=5),
        fill="tozeroy", fillcolor="rgba(214,39,40,0.08)",
    ))
    fig.update_layout(
        title="Resting Heart Rate",
        xaxis=dict(tickformat="%b %d"),
        yaxis=dict(title="bpm"),
        margin=dict(t=50, b=30, l=50, r=20),
        height=300, showlegend=False,
    )
    return fig


_QUALIFIER_COLORS = {
    "EXCELLENT": "#2ca02c", "GOOD": "#98df8a",
    "FAIR_PLUS": "#dbdb8d", "FAIR": "#ffbb78",
    "FAIR_MINUS": "#ff9896", "POOR": "#d62728",
}


def chart_sleep_score(df: pd.DataFrame) -> go.Figure:
    colors = [
        _QUALIFIER_COLORS.get(str(q).upper().strip(), "#aec7e8")
        for q in df.get("sleep_score_qualifier", [])
    ]
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["sleep_score"],
        marker_color=colors,
        hovertemplate="Score: %{y}<br>%{customdata}<extra></extra>",
        customdata=df.get("sleep_score_qualifier", []),
    ))
    fig.add_hline(y=70, line_dash="dot", line_color="green",
                  annotation_text="Good threshold", annotation_position="top right")
    fig.update_layout(
        title="Sleep Score",
        xaxis=dict(tickformat="%b %d"),
        yaxis=dict(title="Score", range=[0, 100]),
        margin=dict(t=50, b=30, l=50, r=20),
        height=300, showlegend=False,
    )
    return fig


def chart_hydration(df: pd.DataFrame) -> go.Figure:
    bar_colors = [
        "#1f77b4" if (pct or 0) < 80 else "#2ca02c"
        for pct in df["pct_of_goal"]
    ]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["date"], y=df["intake_ml"],
        name="Intake (ml)",
        marker_color=bar_colors,
        hovertemplate="%{y:.0f} ml (%{customdata:.0f}% of goal)<extra></extra>",
        customdata=df["pct_of_goal"],
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["goal_ml"],
        name="Goal (ml)", mode="lines",
        line=dict(color="#17becf", width=1.5, dash="dot"),
        hovertemplate="Goal: %{y:.0f} ml<extra></extra>",
    ))
    sweat = df[df["sweat_loss_ml"].notna() & (df["sweat_loss_ml"] > 0)]
    if not sweat.empty:
        fig.add_trace(go.Scatter(
            x=sweat["date"], y=sweat["sweat_loss_ml"],
            name="Sweat loss (ml)", mode="markers",
            marker=dict(color="#ff7f0e", size=8, symbol="triangle-up"),
            hovertemplate="Sweat: %{y:.0f} ml<extra></extra>",
        ))
    fig.update_layout(
        title="Hydration",
        xaxis=dict(tickformat="%b %d"),
        yaxis=dict(title="ml", rangemode="tozero"),
        legend=dict(orientation="h", y=1.15, x=0),
        margin=dict(t=55, b=30, l=55, r=20),
        height=300,
    )
    return fig


def chart_weight(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["weight_kg"],
        mode="lines+markers",
        line=dict(color="#a78bfa", width=2),
        marker=dict(size=7),
        hovertemplate="%{x|%b %d}: <b>%{y:.1f} kg</b><extra></extra>",
    ))
    if len(df) >= 2:
        import numpy as np
        x_num = (df["date"] - df["date"].min()).dt.days.values
        coeffs = np.polyfit(x_num, df["weight_kg"].values, 1)
        trend_y = np.polyval(coeffs, x_num)
        fig.add_trace(go.Scatter(
            x=df["date"], y=trend_y,
            mode="lines", name="Trend",
            line=dict(color="#f472b6", width=1.5, dash="dot"),
            hoverinfo="skip",
        ))
    fig.update_layout(
        title="Weight (kg)",
        xaxis=dict(tickformat="%b %d"),
        yaxis=dict(title="kg"),
        margin=dict(t=50, b=30, l=55, r=20),
        height=300, showlegend=False,
    )
    return fig


# ── Dashboard tab ──────────────────────────────────────────────────────────────

def _render_race_countdown(athlete_config: dict) -> None:
    race_date_str = athlete_config.get("target_race_date")
    target_pace = athlete_config.get("target_pace_min_per_km")
    shoes = athlete_config.get("shoes", [])

    has_race = race_date_str or target_pace
    has_shoes = bool(shoes)
    if not has_race and not has_shoes:
        return

    cols = []
    if has_race:
        cols = st.columns(2 + len(shoes)) if has_shoes else st.columns(2)
    else:
        cols = st.columns(len(shoes))

    col_idx = 0
    if race_date_str:
        try:
            race_date = datetime.date.fromisoformat(race_date_str)
            days_left = (race_date - datetime.date.today()).days
            label = f"{days_left}d to race" if days_left >= 0 else "Race day passed"
            cols[col_idx].metric("Target Race", race_date.strftime("%b %d, %Y"), delta=label)
            col_idx += 1
        except ValueError:
            pass

    if target_pace:
        total_sec = round(target_pace * 60)
        pace_str = f"{total_sec // 60}:{total_sec % 60:02d} min/km"
        cols[col_idx].metric("Target Pace", pace_str)
        col_idx += 1

    db = get_db()
    for shoe in shoes:
        start = shoe.get("start_date", "")
        max_km = shoe.get("max_km", 600)
        if not start or col_idx >= len(cols):
            break
        km_used = db.get_km_since(start)
        pct = min(km_used / max_km, 1.0) if max_km else 0.0
        color = "green" if pct < 0.70 else ("orange" if pct < 0.90 else "red")
        with cols[col_idx]:
            st.markdown(f"**{shoe['name']}**")
            st.progress(pct, text=f":{color}[{km_used:.0f} / {max_km} km]")
        col_idx += 1

    # ── Garmin race predictions ───────────────────────────────────────────────
    predictions = get_race_predictions()
    if predictions:
        st.markdown("**Garmin Race Predictions**")
        p_cols = st.columns(4)
        distances = [
            ("5K",            predictions.get("5k", {}),            None),
            ("10K",           predictions.get("10k", {}),           None),
            ("Half Marathon", predictions.get("half_marathon", {}), target_pace),
            ("Marathon",      predictions.get("marathon", {}),      None),
        ]
        for col, (label, pred, t_pace) in zip(p_cols, distances):
            formatted = pred.get("formatted", "—")
            delta_str = None
            delta_color = "off"
            if label == "Half Marathon" and t_pace:
                target_sec = round(t_pace * 60 * 21.0975)
                pred_sec = pred.get("seconds") or 0
                if pred_sec:
                    diff = pred_sec - target_sec
                    abs_diff = abs(diff)
                    sign = "+" if diff > 0 else "-"
                    diff_str = f"{abs_diff // 60}:{abs_diff % 60:02d}"
                    delta_str = f"{sign}{diff_str} vs target"
                    delta_color = "normal"
            col.metric(label, formatted, delta=delta_str,
                       delta_color=delta_color if delta_str else "off")
        as_of = predictions.get("as_of_date", "")
        if as_of:
            st.caption(f"As of {as_of} · Updated on each Sync")

    st.divider()


def render_dashboard(
    runs_df: pd.DataFrame,
    health_df: pd.DataFrame,
    intensity_df: pd.DataFrame,
    load_df: pd.DataFrame,
    efficiency_df: pd.DataFrame,
    hydration_df: pd.DataFrame,
    weight_df: pd.DataFrame,
    athlete_config: dict,
) -> None:
    if runs_df.empty and health_df.empty:
        st.info("No data found. Run the data pipeline first.")
        return

    _render_race_countdown(athlete_config)

    # ── Row 1: Load Balance | Intensity Distribution ───────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        _section_header("Load Balance", "load_balance")
        if not load_df.empty:
            st.plotly_chart(chart_load_balance(load_df), use_container_width=True)
            _load_balance_summary(load_df)
        elif not athlete_config.get("lthr"):
            st.info("Set LTHR in Athlete Config to enable ATL / CTL / TSB.")
        else:
            st.warning("No training load data yet.")

    with col_right:
        lthr = int(athlete_config.get("lthr", 0))
        zone_source = intensity_df["source"].iloc[0] if not intensity_df.empty and "source" in intensity_df.columns else "friel_fallback"
        _section_header("Intensity Distribution", "intensity_distribution",
                        tooltip_text=_intensity_tooltip(lthr, zone_source=zone_source))
        if not intensity_df.empty and intensity_df["total_min"].sum() > 0:
            st.plotly_chart(chart_intensity_distribution(intensity_df, lthr=lthr),
                            use_container_width=True)
            latest_week = intensity_df.iloc[-1]
            total = latest_week["total_min"]
            if total > 0:
                easy_pct = (latest_week["z1_min"] + latest_week["z2_min"]) / total * 100
                hard_pct = (latest_week["z4_min"] + latest_week["z5_min"]) / total * 100
                color = "green" if easy_pct >= 75 else ("orange" if easy_pct >= 60 else "red")
                st.markdown(
                    f"This week: :{color}[**{easy_pct:.0f}% easy**] · "
                    f"{latest_week['z3_min']:.0f} min tempo · "
                    f"{hard_pct:.0f}% hard · **{total:.0f} min total**"
                )
        else:
            st.warning("No run data for intensity zones.")

    st.divider()

    # ── Row 2: Run Efficiency (full width) ────────────────────────────────────
    _section_header("Run Efficiency", "run_efficiency")
    if not efficiency_df.empty:
        st.plotly_chart(chart_run_efficiency(efficiency_df), use_container_width=True)
        _efficiency_summary(efficiency_df)
    else:
        st.info("No easy runs found yet — needs at least one run with Z1+Z2 ≥ 70%.")

    st.divider()

    # ── Row 3: Historical trends ───────────────────────────────────────────────
    st.subheader("Historical Trends")
    _section_header("HRV & Body Battery", "hrv_battery")
    if not health_df.empty:
        st.plotly_chart(chart_hrv_body_battery(health_df), use_container_width=True)
    else:
        st.warning("No HRV / Body Battery data.")

    h_col3, h_col4, h_col5, h_col6 = st.columns(4)
    with h_col3:
        _section_header("Resting Heart Rate", "resting_hr")
        if not health_df.empty:
            st.plotly_chart(chart_resting_hr(health_df), use_container_width=True)
        else:
            st.warning("No heart rate data.")
    with h_col4:
        _section_header("Sleep Score", "sleep_score")
        if not health_df.empty:
            st.plotly_chart(chart_sleep_score(health_df), use_container_width=True)
        else:
            st.warning("No sleep data.")
    with h_col5:
        _section_header("Hydration", "hydration")
        if not hydration_df.empty:
            st.plotly_chart(chart_hydration(hydration_df), use_container_width=True)
            latest_h = hydration_df.iloc[-1]
            pct = latest_h["pct_of_goal"] or 0
            intake = int(latest_h["intake_ml"] or 0)
            goal = int(latest_h["goal_ml"] or 0)
            color = "green" if pct >= 80 else ("orange" if pct >= 50 else "red")
            st.markdown(
                f"Today: :{color}[**{intake} ml**] of {goal} ml goal "
                f"({pct:.0f}%)"
            )
        else:
            st.info("No hydration data. Sync to pull from Garmin.")
    with h_col6:
        _section_header("Weight", "weight")
        if not weight_df.empty:
            st.plotly_chart(chart_weight(weight_df), use_container_width=True)
            latest_w = weight_df.iloc[-1]
            delta_kg = round(latest_w["weight_kg"] - weight_df.iloc[0]["weight_kg"], 2) if len(weight_df) > 1 else None
            delta_str = ""
            if delta_kg is not None:
                sign = "+" if delta_kg > 0 else ""
                color = "red" if delta_kg > 0 else "green"
                delta_str = f" (:{color}[{sign}{delta_kg} kg over 30d])"
            st.markdown(f"Latest: **{latest_w['weight_kg']:.1f} kg**{delta_str}")
        else:
            st.info("No weight data. Log weight in Garmin Connect.")


# ── Chat tab ───────────────────────────────────────────────────────────────────

_JARVIS_URL = "http://localhost:7934"


def render_chat() -> None:
    components.iframe(_JARVIS_URL, height=700, scrolling=False)


# ── App entry point ────────────────────────────────────────────────────────────

runs_df = load_runs(20)
health_df = load_health(30)
hydration_df = load_hydration(30)
weight_df = load_weight(30)

render_sidebar(health_df)

tab_dash, tab_chat = st.tabs(["Dashboard", "J.A.R.V.I.S."])
with tab_dash:
    athlete_config = render_athlete_config_expander()
    lthr = int(athlete_config.get("lthr", 0))
    intensity_df = load_weekly_intensity(2, lthr=lthr)
    efficiency_df = load_efficiency_trend(16, lthr=lthr)
    load_df = load_training_load(lthr) if lthr > 0 else pd.DataFrame()
    render_dashboard(runs_df, health_df, intensity_df, load_df, efficiency_df, hydration_df, weight_df, athlete_config)
with tab_chat:
    render_chat()
