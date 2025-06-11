import streamlit as st
import requests
import json
from datetime import datetime
import logging
import os
import pickle

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
CACHE_DIR = ".canvas_cache"
TERMS_CACHE_FILE = os.path.join(CACHE_DIR, "terms.pkl")
CACHE_TTL_HOURS = 24

# --- Streamlit UI Configuration ---
st.set_page_config(layout="wide", page_title="Canvas Course Manager")
st.title("Canvas Course Management Tool")
st.markdown("Manage and reset Canvas course participation settings.")

# --- Session State Initialization ---
if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False

# --- Credential Inputs ---
with st.expander("Canvas Credentials", expanded=not st.session_state.data_loaded):
    canvas_domain = st.text_input("Canvas Domain", placeholder="yourdomain.instructure.com")
    api_token = st.text_input("Canvas API Token", type="password")
    account_id = st.text_input("Canvas Account ID", placeholder="1")

base_url = f"https://{canvas_domain}"
headers = {"Authorization": f"Bearer {api_token}"}

# --- API Helpers ---
def _paginated_get_from_api(url: str, headers: dict) -> list[dict]:
    all_data = []
    while url:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            break
        data = resp.json()
        all_data.extend(data.get('enrollment_terms', data) if isinstance(data, dict) else data)
        links = resp.headers.get('Link', '').split(',')
        url = next((l.split(';')[0].strip('<>') for l in links if 'rel="next"' in l), None)
    return all_data

# --- Load Terms ---
if canvas_domain and api_token and account_id and not st.session_state.data_loaded:
    url = f"{base_url}/api/v1/accounts/{account_id}/terms?per_page=100"
    with st.spinner("Loading terms..."):
        terms = _paginated_get_from_api(url, headers)
    if not terms:
        st.error("No terms returned. Check your API token or account ID.")
        st.stop()
    st.session_state.terms = terms
    st.session_state.data_loaded = True
    st.rerun()

# --- Term Selection ---
if st.session_state.data_loaded:
    term_options = st.session_state.terms
    term_names = ["--- Select a Term ---"] + [f"{term['name']} (ID: {term['id']})" for term in term_options]
    selected_index = st.selectbox("Select a Term", list(range(len(term_names))), format_func=lambda i: term_names[i])

    if selected_index != 0:
        selected_term = term_options[selected_index - 1]
        term_id = selected_term['id']

        # --- Fetch Courses by Term ---
        url = f"{base_url}/api/v1/accounts/{account_id}/courses?enrollment_term_id={term_id}&per_page=100&include[]=enrollments"
        with st.spinner("Fetching courses for selected term..."):
            all_courses = _paginated_get_from_api(url, headers)

        if not all_courses:
            st.warning("No courses returned for this term.")
            st.stop()

        # --- Filter courses with participation override AND enrollments ---
        filtered_courses = []
        for course in all_courses:
            enrollments = course.get("enrollments", [])
            has_students = any(e.get("type", "").lower() == "student" for e in enrollments)
            if course.get("restrict_enrollments_to_course_dates") and has_students:
                if course.get("start_at") or course.get("end_at"):
                    filtered_courses.append(course)

        if filtered_courses:
            st.success(f"Found {len(filtered_courses)} courses with custom dates and active student enrollments.")
            st.json(filtered_courses[:5])
        else:
            st.info("No courses matched the filter criteria.")
