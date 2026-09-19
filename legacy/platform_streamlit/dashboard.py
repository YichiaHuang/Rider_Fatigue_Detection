"""Rider fatigue dashboard (plan.md section 4.4 / section 5).

Shows 5 riders — 1 live board + 4 simulated (platform/simulator.py) — their
fatigue score curves, current status, and dispatch pause/resume state.
Run: streamlit run dashboard.py
(also run: `mosquitto`, `python simulator.py`, and the real rider's
mqtt_publisher / Stage A pipeline, or `python ../rider/mqtt_publisher.py
--rider-id 7` to smoke-test with a fake live rider.)
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from circuit_breaker import BreakerConfig, DispatchCircuitBreaker
from mqtt_subscriber import RiderScoreSubscriber

st.set_page_config(page_title="騎手疲勞儀表板", layout="wide")

# Fixed rider display order + categorical color assignment (dataviz skill:
# categorical hues in fixed order, never cycled/reassigned by rank).
RIDER_ORDER = ["7", "sim-1", "sim-2", "sim-3", "sim-4"]
RIDER_LABELS = {
    "7": "7 號騎手（現場實機）",
    "sim-1": "模擬騎手 1",
    "sim-2": "模擬騎手 2",
    "sim-3": "模擬騎手 3",
    "sim-4": "模擬騎手 4",
}
CATEGORICAL_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"
MUTED_INK = "#898781"


@st.cache_resource
def get_breaker() -> DispatchCircuitBreaker:
    breaker = DispatchCircuitBreaker(BreakerConfig())

    def _run():
        sub = RiderScoreSubscriber(breaker, broker_host="localhost", broker_port=1883)
        sub.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return breaker


def render() -> None:
    breaker = get_breaker()

    st.title("騎手疲勞儀表板")
    st.caption("分數為疲勞指數（0–100），僅傳數值，不含影像或位置。")

    fig = go.Figure()
    cols = st.columns(len(RIDER_ORDER))

    for i, rider_id in enumerate(RIDER_ORDER):
        color = CATEGORICAL_COLORS[i]
        state = breaker.get_state(rider_id)

        if state is not None and state.history:
            df = pd.DataFrame(state.history, columns=["timestamp", "score"])
            df["t"] = pd.to_datetime(df["timestamp"], unit="s")
            fig.add_trace(go.Scatter(
                x=df["t"], y=df["score"],
                mode="lines", name=RIDER_LABELS[rider_id],
                line=dict(color=color, width=2),
            ))

        with cols[i]:
            st.markdown(f"**{RIDER_LABELS[rider_id]}**")
            if state is None:
                st.markdown(f":gray[● 尚無資料]")
                continue
            score_display = f"{state.score:.1f}"
            if state.paused:
                st.markdown(
                    f"<span style='color:{STATUS_CRITICAL}'>🔴 建議暫停派單</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<span style='color:{STATUS_GOOD}'>🟢 正常派單</span>",
                    unsafe_allow_html=True,
                )
            st.markdown(f"<span style='font-variant-numeric: tabular-nums; font-size: 1.4rem'>{score_display}</span>",
                        unsafe_allow_html=True)
            if breaker.is_stale(rider_id):
                st.caption("⚠️ 訊號超過 30 秒未更新")

    fig.update_layout(
        yaxis=dict(title="疲勞指數", range=[0, 100], gridcolor="#e1e0d9"),
        xaxis=dict(title="時間", gridcolor="#e1e0d9"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=40, r=20, t=40, b=40),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("原始資料表"):
        rows = []
        for rider_id in RIDER_ORDER:
            state = breaker.get_state(rider_id)
            if state is None:
                continue
            rows.append({
                "rider_id": rider_id,
                "score": state.score,
                "paused": state.paused,
                "last_update": time.strftime("%H:%M:%S", time.localtime(state.last_update)),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)


render()
time.sleep(1.0)
st.rerun()
