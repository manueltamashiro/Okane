"""Configuration page — editable strategy YAML + read-only risk limits."""

from __future__ import annotations

import streamlit as st

from config.settings import (
    DAILY_LOSS_LIMIT,
    MAX_CONCURRENT_POSITIONS,
    MAX_DRAWDOWN,
    MAX_POSITION_SIZE_PCT,
    MAX_RISK_PER_TRADE,
    MAX_SECTOR_EXPOSURE,
    VIX_BRAKE_THRESHOLD,
    WEEKLY_LOSS_LIMIT,
)
from monitoring.dashboard.cached_data import load_strategies_yaml, save_strategies_yaml


def _render_param_input(key_prefix: str, param_name: str, value):
    """Render the appropriate Streamlit input widget based on value type."""
    widget_key = f"{key_prefix}__{param_name}"
    if isinstance(value, bool):
        return st.checkbox(param_name, value=value, key=widget_key)
    elif isinstance(value, (int, float)):
        return st.number_input(
            param_name,
            value=value,
            format="%f" if isinstance(value, float) else "%d",
            key=widget_key,
        )
    elif isinstance(value, str):
        return st.text_input(param_name, value=value, key=widget_key)
    elif isinstance(value, list):
        return st.text_input(
            param_name,
            value=", ".join(str(v) for v in value),
            help="Comma-separated list",
            key=widget_key,
        )
    elif isinstance(value, dict):
        st.markdown(f"**{param_name}**")
        updated = {}
        for sub_key, sub_val in value.items():
            updated[sub_key] = _render_param_input(f"{key_prefix}__{param_name}", sub_key, sub_val)
        return updated
    else:
        return st.text_input(param_name, value=str(value), key=widget_key)


def _parse_param_value(original, new_value):
    """Convert widget output back to the original type."""
    if isinstance(original, bool):
        return bool(new_value)
    elif isinstance(original, int):
        return int(new_value)
    elif isinstance(original, float):
        return float(new_value)
    elif isinstance(original, list):
        if isinstance(new_value, str):
            parts = [p.strip() for p in new_value.split(",") if p.strip()]
            if original and isinstance(original[0], (int, float)):
                try:
                    if isinstance(original[0], int):
                        return [int(p) for p in parts]
                    return [float(p) for p in parts]
                except ValueError:
                    pass
            return parts
        return new_value
    elif isinstance(original, dict):
        return new_value
    return new_value


def render_configuration() -> None:
    st.header("Configuration")

    # ---- Strategy Parameters (editable) ----
    st.subheader("Strategy Parameters")
    strat_data = load_strategies_yaml()

    if not strat_data:
        st.warning("Could not load strategies.yaml")
    else:
        updated_data = dict(strat_data)

        for section_key, section_val in strat_data.items():
            if not isinstance(section_val, dict):
                continue

            with st.expander(section_key, expanded=False):
                with st.form(key=f"form__{section_key}"):
                    new_section = {}
                    for param_name, param_val in section_val.items():
                        new_val = _render_param_input(section_key, param_name, param_val)
                        new_section[param_name] = _parse_param_value(param_val, new_val)

                    submitted = st.form_submit_button("Save")
                    if submitted:
                        updated_data[section_key] = new_section
                        try:
                            save_strategies_yaml(updated_data)
                            st.success(f"Saved {section_key} parameters")
                        except Exception as exc:
                            st.error(f"Failed to save: {exc}")

    st.divider()

    # ---- Risk Limits (read-only) ----
    st.subheader("Risk Limits (read-only)")
    r1, r2, r3 = st.columns(3)
    r1.metric("Max Risk Per Trade", f"{MAX_RISK_PER_TRADE:.0%}")
    r2.metric("Max Position Size", f"{MAX_POSITION_SIZE_PCT:.0%}")
    r3.metric("Daily Loss Limit", f"{DAILY_LOSS_LIMIT:.0%}")

    r4, r5, r6 = st.columns(3)
    r4.metric("Weekly Loss Limit", f"{WEEKLY_LOSS_LIMIT:.0%}")
    r5.metric("Max Drawdown", f"{MAX_DRAWDOWN:.0%}")
    r6.metric("Max Concurrent Positions", str(MAX_CONCURRENT_POSITIONS))

    r7, r8 = st.columns(2)
    r7.metric("Max Sector Exposure", str(MAX_SECTOR_EXPOSURE))
    r8.metric("VIX Brake Threshold", f"{VIX_BRAKE_THRESHOLD:.0f}")
