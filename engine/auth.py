# engine/auth.py
# KNCS LeadGen Pro — Authentication & Access Control

import os
import streamlit as st

def _read_env_file():
    """Reads .env directly from disk so updates work immediately."""
    env_vars = {}
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        ".env"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            env_vars[k.strip()] = v.strip().strip("'\"")
                break
            except Exception:
                pass
    return env_vars

def _get_auth_config():
    """Retrieve auth settings from Streamlit secrets, .env file, or environment."""
    file_env = _read_env_file()

    def _lookup(k, fallback):
        # 1. First check Streamlit Cloud Secrets
        try:
            if hasattr(st, "secrets") and k in st.secrets:
                return str(st.secrets[k])
        except Exception:
            pass
        # 2. Then check local .env file or system environment variables
        return file_env.get(k) or os.getenv(k, fallback)

    enabled_str = _lookup("APP_AUTH_ENABLED", "True")
    enabled = str(enabled_str).strip().lower() in ("true", "1", "yes")

    admin_user = _lookup("APP_ADMIN_USER", "amechi")
    admin_pass = _lookup("APP_ADMIN_PASSWORD", "kncspro2026")

    return enabled, str(admin_user).strip(), str(admin_pass).strip()

def _verify_credentials(username: str, password: str, admin_user: str, admin_pass: str) -> bool:
    """Verify username and password."""
    if not username or not password:
        return False
    return (username.strip().lower() == admin_user.lower()) and (password.strip() == admin_pass)

def require_auth() -> bool:
    """Guards the app behind a login screen."""
    enabled, admin_user, admin_pass = _get_auth_config()
    
    if not enabled:
        return True
        
    if st.session_state.get("authenticated", False):
        return True

    # Hide sidebar while on login screen
    st.markdown("""
        <style>
        [data-testid="stSidebar"] {
            display: none !important;
        }
        .stApp {
            background: radial-gradient(circle at 50% 20%, #0d1726 0%, #050a12 100%);
        }
        </style>
    """, unsafe_allow_html=True)

    # Center-aligned Login Container
    _, col_mid, _ = st.columns([1, 1.4, 1])

    with col_mid:
        st.markdown("<div style='height: 60px;'></div>", unsafe_allow_html=True)
        
        st.markdown("""
            <div style="text-align: center; margin-bottom: 24px;">
                <div style="
                    display: inline-block;
                    padding: 8px 18px;
                    background: rgba(30, 77, 140, 0.25);
                    border: 1px solid rgba(56, 189, 248, 0.4);
                    border-radius: 30px;
                    color: #38bdf8;
                    font-size: 13px;
                    font-weight: 700;
                    letter-spacing: 1.5px;
                    margin-bottom: 12px;
                ">KNCS LEAD INTELLIGENCE</div>
                <h1 style="color: #ffffff; font-size: 28px; font-weight: 800; margin: 0;">Portal Sign In</h1>
                <p style="color: #94a3b8; font-size: 14px; margin-top: 6px;">Secure access for authorized sales reps & administrators</p>
            </div>
        """, unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
            user_input = st.text_input("Username", placeholder="e.g. amechi", key="login_username")
            pass_input = st.text_input("Password", type="password", placeholder="••••••••••••", key="login_password")
            
            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            
            submit_btn = st.button("Sign In to Portal →", type="primary", use_container_width=True)
            
            if submit_btn:
                if _verify_credentials(user_input, pass_input, admin_user, admin_pass):
                    st.session_state["authenticated"] = True
                    st.session_state["user"] = user_input.strip()
                    st.rerun()
                else:
                    st.error("Invalid username or password. Please try again.")

        st.markdown("""
            <div style="text-align: center; color: #475569; font-size: 12px; margin-top: 24px;">
                King Networks Consulting Services (KNCS) &copy; 2026. All rights reserved.
            </div>
        """, unsafe_allow_html=True)

    return False

def render_logout_widget():
    """Renders user info and a logout button pinned inside the sidebar."""
    enabled, _, _ = _get_auth_config()
    if not enabled:
        return
        
    user = st.session_state.get("user", "Admin")
    with st.sidebar:
        st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
        st.markdown(f"""
            <div style="
                padding: 8px 12px;
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                font-size: 12px;
                color: #94a3b8;
                margin-bottom: 8px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            ">
                <span>👤 User: <b style="color: #38bdf8;">{user}</b></span>
            </div>
        """, unsafe_allow_html=True)
        
        if st.button("Log Out 🚪", key="auth_logout_btn", use_container_width=True):
            st.session_state["authenticated"] = False
            st.session_state.pop("user", None)
            st.rerun()