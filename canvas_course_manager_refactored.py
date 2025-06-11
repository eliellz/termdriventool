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
COURSES_CACHE_FILE_PREFIX = os.path.join(CACHE_DIR, "courses_term_")
CACHE_TTL_HOURS = 24
COURSE_DISPLAY_LIMIT = 5

# --- Streamlit UI Configuration ---
st.set_page_config(layout="wide", page_title="Canvas Course Manager")
st.title("Canvas Course Management Tool")
st.markdown("Manage and reset Canvas course participation settings.")

# --- Session State Initialization ---
INITIAL_SESSION_STATE = {
    'course_selections': {},
    'select_all_courses': False,
    'cached_enrollment_counts': {},
    'fetched_terms': [],
    'fetched_courses_by_term': {},
    'selected_term_id': None,
    'current_display_count': COURSE_DISPLAY_LIMIT,
    'data_loaded_and_terms_fetched': False,
    'courses_search_triggered_for_term': False,
    'last_api_token_used': "",
    'last_account_id_used': "",
    'current_selected_term_name': "--- Select a Term ---",
    'last_filtered_courses_cache': [],
    'last_filtered_term_id': None,
    'show_filtering_debug_info': False,
    'app_needs_reset': False
}
for key, default_value in INITIAL_SESSION_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

# --- Credential Inputs ---
with st.expander("🔐 Canvas Credentials", expanded=not st.session_state.data_loaded_and_terms_fetched):
    canvas_domain = st.text_input("Canvas Domain", placeholder="yourdomain.instructure.com")
    api_token = st.text_input("Canvas API Token", type="password")
    account_id = st.text_input("Canvas Account ID", placeholder="1")
base_url = f"https://{canvas_domain}"
headers = {"Authorization": f"Bearer {api_token}"}

# --- API Helpers ---
def _paginated_get_from_api(url: str, headers: dict) -> list[dict]:
    print(f"🔍 Requesting: {url}")  # ✅ fixed indent
    all_data = []
    current_url = url
    while current_url:
        resp = requests.get(current_url, headers=headers)
        if resp.status_code != 200:
            break
        data = resp.json()
        all_data.extend(data.get('enrollment_terms', data) if isinstance(data, dict) else data)
        links = resp.headers.get('Link', '').split(',')
        current_url = next((l.split(';')[0].strip('<>') for l in links if 'rel=\"next\"' in l), None)
    return all_data

def get_enrollment_count(course_id: str, base_url: str, headers: dict) -> int:
    url = f"{base_url}/api/v1/courses/{course_id}/enrollments?type[]=StudentEnrollment&state[]=active"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        return len(resp.json())
    return 0

# --- File-Based Cache ---
def _load_from_file_cache(filepath: str):
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f:
            data, timestamp = pickle.load(f)
        if (datetime.now() - timestamp).total_seconds() / 3600 < CACHE_TTL_HOURS:
            return data, timestamp
    return None, None

def _save_to_file_cache(filepath: str, data: list[dict]):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(filepath, 'wb') as f:
        pickle.dump((data, datetime.now()), f)

# --- Participation Settings UI ---
def participation_settings_ui(course_ids: list[str], courses: list[dict], key_prefix: str = "") -> list[dict]:
    settings = []
    for course_id in course_ids:
        course_name = next((c["name"] for c in courses if str(c["id"]) == course_id), course_id)
        with st.expander(f"Participation Settings: {course_name} (ID: {course_id})", expanded=False):
            mode = st.radio("Participation Mode", ["Term Driven", "Date Driven"], key=f"{key_prefix}mode_{course_id}")
            start_date, end_date = None, None
            if mode == "Date Driven":
                start_date = st.date_input("Start Date", key=f"{key_prefix}start_{course_id}")
                if st.checkbox("No End Date", key=f"{key_prefix}no_end_{course_id}"):
                    end_date = None
                else:
                    end_date = st.date_input("End Date", key=f"{key_prefix}end_{course_id}")
            settings.append({"course_id": course_id, "mode": mode, "start_date": start_date, "end_date": end_date})
    return settings

# --- Apply Settings ---
def apply_participation_settings(base_url: str, selected_courses: list[dict], headers: dict):
    if not selected_courses:
        st.info("No courses selected.")
        return
    st.subheader("📤 Applying Participation Settings")
    total = len(selected_courses)
    progress = st.progress(0)
    for i, course in enumerate(selected_courses):
        url = f"{base_url}/api/v1/courses/{course['course_id']}"
        payload = {
            "course": {
                "start_at": f"{course['start_date']}T00:00:00Z" if course['mode'] == "Date Driven" and course['start_date'] else None,
                "end_at": f"{course['end_date']}T23:59:59Z" if course['mode'] == "Date Driven" and course['end_date'] else None,
                "restrict_enrollments_to_course_dates": course['mode'] == "Date Driven"
            },
            "override_sis_stickiness": True
        }
        try:
            resp = requests.put(url, headers=headers, json=payload)
            resp.raise_for_status()
            st.success(f"✅ Updated course {course['course_id']}")
        except Exception as e:
            st.error(f"❌ Failed to update course {course['course_id']}: {e}")
        progress.progress((i + 1) / total)
    st.success("🎉 All selected courses have been processed.")

# --- Term and Course Selection Workflow ---
if canvas_domain and api_token and account_id:
    if st.button("🚀 Load Canvas Terms"):
        terms, _ = _load_from_file_cache(TERMS_CACHE_FILE)
        if not terms:
            url = f"{base_url}/api/v1/accounts/{account_id}/terms?per_page=100"
            terms = _paginated_get_from_api(url, headers)
            _save_to_file_cache(TERMS_CACHE_FILE, terms)
        st.session_state.fetched_terms = terms
        st.session_state.data_loaded_and_terms_fetched = True
        st.rerun()

    if st.session_state.data_loaded_and_terms_fetched and st.session_state.fetched_terms:
        st.success("✅ Terms loaded successfully!")
        term_names = ["--- Select a Term ---"] + [f"{term['name']} (ID: {term['id']})" for term in st.session_state.fetched_terms]
        selected_index = st.selectbox("Select a Term", list(range(len(term_names))), format_func=lambda i: term_names[i])

        if selected_index != 0:
            selected_term = st.session_state.fetched_terms[selected_index - 1]
            st.session_state.selected_term_id = selected_term['id']

# --- Use Selected Term ---
term_options = st.session_state.fetched_terms
selected_term = next((t for t in term_options if t["id"] == st.session_state.selected_term_id), None)

if not selected_term:
    st.error("❌ No term selected. Please select a term above.")
    st.stop()


# --- Fetch and Filter Courses ---
from datetime import datetime

url = (  # ✅ Replace this line
    f"{base_url}/api/v1/accounts/{account_id}/courses"
    f"?enrollment_term_id={selected_term['id']}&per_page=100"
    f"&include[]=enrollments"
    f"&enrollment_type[]=StudentEnrollment"
    f"&state[]=all"
)
with st.spinner("Fetching courses for selected term..."):
    all_courses = _paginated_get_from_api(url, headers)

# Fetch and display total courses
st.write(f"📦 Total courses fetched: {len(all_courses)}")
for course in all_courses[:5]:
    st.markdown(f"**Course ID:** {course.get('id')}")
    st.write("Enrollments:", course.get("enrollments", "⚠️ Missing"))

# --- Filter courses ---
filtered_courses = []
for course in all_courses:
    restrict = course.get("restrict_enrollments_to_course_dates", False)
    start_at = course.get("start_at")
    end_at = course.get("end_at")
    enrollments = course.get("enrollments", [])

    if restrict and (start_at or end_at) and enrollments:
        filtered_courses.append(course)

# --- Display Matching Courses ---
if filtered_courses:
    st.success(f"✅ {len(filtered_courses)} courses with mismatched dates and active enrollments found.")

    for course in filtered_courses:
        course_id = str(course['id'])
        if f"select_{course_id}" not in st.session_state:
            st.session_state[f"select_{course_id}"] = False

    # --- Course Selection UI ---
    st.markdown("### 📋 Step 1: Select Courses to Update")
    st.info("Scroll through the list of courses below and check the ones you'd like to update.")

    col_select_all, col_deselect_all = st.columns(2)
    with col_select_all:
        select_all = st.checkbox("✅ Select All Courses")
    with col_deselect_all:
        deselect_all = st.checkbox("🚫 Deselect All Courses")

    selected_course_ids = []

    for course in filtered_courses:
        course_id = str(course['id'])

        if deselect_all:
            checked = False
        elif select_all:
            checked = True
        else:
            checked = st.session_state.get(f"select_{course_id}", False)

        with st.container():
            col1, col2 = st.columns([0.05, 0.95])
            with col1:
                checked = st.checkbox("", key=f"select_{course_id}", value=checked)
            with col2:
                with st.expander(f"📘 {course['name']} (ID: {course_id})", expanded=False):
                    st.markdown(f"**🧑‍🎓 Active Enrollments:** {course['_active_enrollments']}")
                    st.markdown(f"**📆 Term ID:** `{course['_term']}`")
                    st.markdown(f"**⚙️ Mode:** `{course['_participation']}`")
                    st.markdown(f"**📅 Start Date:** `{course.get('start_at', 'None')}`")
                    st.markdown(f"**📅 End Date:** `{course.get('end_at', 'None')}`")
                    canvas_link = f"https://{canvas_domain}/courses/{course_id}"
                    st.markdown(f"[🔗 Open in Canvas]({canvas_link})")

        if st.session_state.get(f"select_{course_id}", False):
            selected_course_ids.append(course_id)

    # --- Step 2: Participation Settings ---
    if selected_course_ids:
        if "settings_collapsed" not in st.session_state:
            st.session_state.settings_collapsed = False

        with st.expander("🛠️ Step 2: Define Participation Settings", expanded=not st.session_state.settings_collapsed):
            course_settings = participation_settings_ui(selected_course_ids, filtered_courses)

            if st.button("✅ Apply Settings to Selected Courses"):
                apply_participation_settings(base_url, course_settings, headers)
                st.session_state.settings_collapsed = True  # Collapse after apply
    else:
        st.warning("⚠️ Please select at least one course above to proceed.")
else:
    st.info("No courses found with partial date overrides and active student enrollments.")
