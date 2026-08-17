import os
import streamlit as st


def apply_theme():
    """Reads the custom CSS file and injects it into the Streamlit app."""
    css_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "assets",
        "styles.css"
    )

    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        st.markdown(
            f"<style>{css}</style>",
            unsafe_allow_html=True
        )
    else:
        st.warning("Warning: styles.css not found.")
