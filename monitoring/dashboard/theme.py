"""NYT-style theme for the Okane dashboard.

Exports:
  inject_css()           — injects custom CSS into the Streamlit app
  get_plotly_template()  — returns a Plotly template dict with matching styling
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

NEAR_BLACK = "#121212"
WHITE = "#FFFFFF"
WARM_GRAY = "#F7F7F5"
STEEL_BLUE = "#567B95"
LIGHT_BORDER = "#E0E0E0"
MUTED_RED = "#A4343A"
MUTED_GREEN = "#2D6A4F"

# Plotly colorway — muted, editorial palette
COLORWAY = [
    STEEL_BLUE,   # primary
    "#D4A574",    # warm tan
    MUTED_RED,    # muted red
    MUTED_GREEN,  # muted green
    "#8B7355",    # brown
    "#6B5B73",    # muted purple
    "#4A7C6F",    # teal
    "#C17B3E",    # amber
]


def inject_css() -> None:
    """Inject NYT-style CSS into the Streamlit app."""
    st.markdown(
        """
        <style>
        /* ---- Google Fonts ---- */
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Sans+3:wght@300;400;600;700&display=swap');

        /* ---- Global typography ---- */
        html, body, [class*="css"] {
            font-family: 'Source Sans 3', 'Source Sans Pro', sans-serif;
            color: #121212;
        }

        /* ---- Headers ---- */
        h1 {
            font-family: 'Playfair Display', Georgia, serif !important;
            font-weight: 900 !important;
            letter-spacing: -0.02em !important;
            border-bottom: 3px double #121212 !important;
            padding-bottom: 0.3em !important;
            margin-bottom: 0.8em !important;
        }

        h2 {
            font-family: 'Playfair Display', Georgia, serif !important;
            font-weight: 700 !important;
            border-bottom: 1px solid #121212 !important;
            padding-bottom: 0.2em !important;
            margin-bottom: 0.6em !important;
        }

        h3, h4, h5, h6 {
            font-family: 'Source Sans 3', sans-serif !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.05em !important;
            font-size: 0.9rem !important;
            color: #555 !important;
        }

        /* ---- Sidebar ---- */
        section[data-testid="stSidebar"] {
            background-color: #121212 !important;
        }

        section[data-testid="stSidebar"] * {
            color: #FFFFFF !important;
        }

        section[data-testid="stSidebar"] .stRadio label {
            font-family: 'Source Sans 3', sans-serif !important;
            font-weight: 400 !important;
            font-size: 1rem !important;
            letter-spacing: 0.02em !important;
            padding: 0.4em 0 !important;
        }

        section[data-testid="stSidebar"] .stRadio label:hover {
            color: #567B95 !important;
        }

        section[data-testid="stSidebar"] h1 {
            font-family: 'Playfair Display', Georgia, serif !important;
            font-weight: 900 !important;
            letter-spacing: 0.05em !important;
            border-bottom: 2px solid #FFFFFF !important;
            padding-bottom: 0.2em !important;
        }

        section[data-testid="stSidebar"] p em {
            font-family: 'Source Sans 3', sans-serif !important;
            font-style: italic !important;
            font-size: 0.85rem !important;
            color: #999 !important;
        }

        /* ---- Metric cards ---- */
        div[data-testid="stMetric"] {
            background-color: #FFFFFF;
            border: 1px solid #E0E0E0;
            border-radius: 0px;
            padding: 1em 1.2em;
        }

        div[data-testid="stMetric"] label {
            font-family: 'Source Sans 3', sans-serif !important;
            text-transform: uppercase !important;
            letter-spacing: 0.08em !important;
            font-size: 0.7rem !important;
            font-weight: 600 !important;
            color: #888 !important;
        }

        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            font-family: 'Playfair Display', Georgia, serif !important;
            font-weight: 700 !important;
            color: #121212 !important;
        }

        /* ---- Tables / DataFrames ---- */
        .stDataFrame thead th {
            font-family: 'Source Sans 3', sans-serif !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
            font-size: 0.75rem !important;
            font-weight: 700 !important;
            border-bottom: 2px solid #121212 !important;
        }

        .stDataFrame tbody td {
            font-family: 'Source Sans 3', sans-serif !important;
            border-bottom: 1px solid #E0E0E0 !important;
            font-size: 0.85rem !important;
        }

        /* ---- Buttons ---- */
        .stButton > button {
            border-radius: 0px !important;
            text-transform: uppercase !important;
            letter-spacing: 0.08em !important;
            font-family: 'Source Sans 3', sans-serif !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
            border: 1px solid #121212 !important;
            transition: all 0.2s ease !important;
        }

        .stButton > button:hover {
            background-color: #121212 !important;
            color: #FFFFFF !important;
        }

        .stButton > button[kind="primary"] {
            background-color: #121212 !important;
            color: #FFFFFF !important;
        }

        .stButton > button[kind="primary"]:hover {
            background-color: #333333 !important;
        }

        /* ---- Forms ---- */
        .stForm {
            border: 1px solid #E0E0E0 !important;
            border-radius: 0px !important;
            padding: 1.5em !important;
        }

        /* ---- Expander ---- */
        .streamlit-expanderHeader {
            font-family: 'Source Sans 3', sans-serif !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.04em !important;
        }

        /* ---- Tabs ---- */
        .stTabs [data-baseweb="tab-list"] {
            border-bottom: 2px solid #121212;
        }

        .stTabs [data-baseweb="tab"] {
            font-family: 'Source Sans 3', sans-serif !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
            font-weight: 600 !important;
            font-size: 0.8rem !important;
        }

        /* ---- Dividers ---- */
        hr {
            border-top: 1px solid #E0E0E0 !important;
            margin: 2em 0 !important;
        }

        /* ---- Success/Error/Warning banners ---- */
        .stAlert {
            border-radius: 0px !important;
            font-family: 'Source Sans 3', sans-serif !important;
        }

        /* ---- Code blocks (for task output) ---- */
        .stCode, code {
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace !important;
            font-size: 0.8rem !important;
        }

        /* ---- Selectbox, multiselect, inputs ---- */
        .stSelectbox, .stMultiSelect, .stTextInput, .stNumberInput, .stDateInput {
            font-family: 'Source Sans 3', sans-serif !important;
        }

        .stSelectbox label, .stMultiSelect label, .stTextInput label,
        .stNumberInput label, .stDateInput label {
            font-family: 'Source Sans 3', sans-serif !important;
            text-transform: uppercase !important;
            letter-spacing: 0.04em !important;
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            color: #888 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_plotly_template() -> go.layout.Template:
    """Return a Plotly template matching the NYT-style dashboard theme."""
    template = go.layout.Template()

    template.layout = go.Layout(
        font=dict(
            family="Source Sans 3, Source Sans Pro, sans-serif",
            color=NEAR_BLACK,
            size=13,
        ),
        title=dict(
            font=dict(
                family="Playfair Display, Georgia, serif",
                size=18,
                color=NEAR_BLACK,
            ),
        ),
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        colorway=COLORWAY,
        xaxis=dict(
            gridcolor=LIGHT_BORDER,
            gridwidth=0.5,
            linecolor=NEAR_BLACK,
            linewidth=1,
            tickfont=dict(size=11),
            title_font=dict(size=12, family="Source Sans 3, sans-serif"),
        ),
        yaxis=dict(
            gridcolor=LIGHT_BORDER,
            gridwidth=0.5,
            linecolor=NEAR_BLACK,
            linewidth=1,
            tickfont=dict(size=11),
            title_font=dict(size=12, family="Source Sans 3, sans-serif"),
        ),
        margin=dict(l=60, r=30, t=50, b=50),
        hoverlabel=dict(
            bgcolor=NEAR_BLACK,
            font_color=WHITE,
            font_size=12,
            font_family="Source Sans 3, sans-serif",
        ),
        legend=dict(
            font=dict(size=11),
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor=LIGHT_BORDER,
            borderwidth=1,
        ),
    )

    return template
