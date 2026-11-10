from __future__ import annotations

from html import escape
from pathlib import Path
import streamlit as st


def _load_stylesheet(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"GDS stylesheet not found: {path}")
    return path.read_text(encoding="utf-8")


def apply_gds_styles(css_path: Path | None = None) -> None:
    """Inject GOV.UK-inspired style overrides for Streamlit widgets."""
    stylesheet_path = css_path or Path(__file__).parent.parent / "styles" / "gds_streamlit.css"
    css = _load_stylesheet(stylesheet_path)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_phase_banner(phase_label: str = "Prototype", text: str = "This is a new service") -> None:
    """Render a simple phase banner matching the GOV.UK look."""
    safe_label = escape(phase_label)
    safe_text = escape(text)
    st.markdown(
        (
            '<div class="govuk-phase-banner">'
            f'<strong class="govuk-tag">{safe_label}</strong>'
            f"<span>{safe_text}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_caption(text: str) -> None:
    st.markdown(f'<span class="govuk-caption-xl">{escape(text)}</span>', unsafe_allow_html=True)


def render_hint(text: str) -> None:
    st.markdown(f'<p class="govuk-hint">{escape(text)}</p>', unsafe_allow_html=True)


def render_inset_text(text: str) -> None:
    st.markdown(f'<div class="govuk-inset-text">{escape(text)}</div>', unsafe_allow_html=True)


def render_section_break() -> None:
    st.markdown('<hr class="govuk-section-break" />', unsafe_allow_html=True)
