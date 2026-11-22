import hmac
import streamlit as st
import os


ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "kadminid"
# Store username/password directly/manually in ECS Fargate environment variables when deployed
# It's easier, no need for AWS Secrets Manager etc.
# Can add a few more username/password combinations for others also
# ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
# ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")


def require_login() -> None:
    if st.session_state.get("authenticated"):
        with st.sidebar:
            st.write(f"Signed in as {st.session_state['username']}")
            if st.button("Sign out"):
                st.session_state.clear()
                st.rerun()
        return

    st.title("Sign in")
    with st.form("login", clear_on_submit=True):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")

    if submitted:
        username_matches = hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode())
        password_matches = hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
        if username_matches and password_matches:
            st.session_state["authenticated"] = True
            st.session_state["username"] = ADMIN_USERNAME
            st.rerun()
        st.error("Incorrect username or password.")
    st.stop()