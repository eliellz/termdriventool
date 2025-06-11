import streamlit as st
import requests
import json
from datetime import datetime
import logging
import os
import pickle

LOG_FILE = "C:/Users/eellison/Documents/canvas_course_change_log.csv"

def log_course_changes_to_csv(log_entries):
    file_exists = os.path.exists(LOG_FILE)

    with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=log_entries[0].keys())
        if not file_exists:
            writer.writeheader()
        writer.writerows(log_entries)

if "trigger_apply" not in st.session_state:
    st.session_state["trigger_apply"] = False

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

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
    'app_needs_reset': False,
    'credentials_collapsed': False,
    'courses_collapsed': False
}
for key, default_value in INITIAL_SESSION_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

# --- API Helpers ---
def _paginated_get_from_api(url: str, headers: dict) -> list[dict]:
    all_data = []
    current_url = url
    while current_url:
        resp = requests.get(current_url, headers=headers)
        if resp.status_code != 200:
            break
        data = resp.json()
        all_data.extend(data.get('enrollment_terms', data) if isinstance(data, dict) else data)
        links = resp.headers.get('Link', '').split(',')
        current_url = next((l.split(';')[0].strip('<>') for l in links if 'rel="next"' in l), None)
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

# --- Step 1: Credentials & Term Selection ---
with st.expander("🔐 Step 1: Canvas Credentials & Term Selection", expanded=not st.session_state.credentials_collapsed):
    st.header("Canvas Credentials")
    user_email = st.text_input("Your **email address**)", key="log_email")
    canvas_domain = st.text_input("Canvas Domain", placeholder="yourdomain.instructure.com")
    api_token = st.text_input("Canvas API Token", type="password")
    account_id = st.text_input("Canvas Account ID", placeholder="1")

    base_url = f"https://{canvas_domain}"
    headers = {"Authorization": f"Bearer {api_token}"}

    if canvas_domain and api_token and account_id:
        if st.button("🚀 Load Canvas Terms"):
            terms, _ = _load_from_file_cache(TERMS_CACHE_FILE)
            if not terms:
                url = f"{base_url}/api/v1/accounts/{account_id}/terms?per_page=100"
                terms = _paginated_get_from_api(url, headers)
                _save_to_file_cache(TERMS_CACHE_FILE, terms)
            st.session_state.fetched_terms = terms
            st.session_state.data_loaded_and_terms_fetched = True
            st.session_state.credentials_collapsed = True
            st.rerun()

# --- Stop if terms not loaded ---
if not st.session_state.data_loaded_and_terms_fetched or not st.session_state.fetched_terms:
    st.stop()

# --- Term Dropdown ---
st.success("✅ Terms loaded successfully!")
term_names = ["--- Select a Term ---"] + [f"{term['name']} (ID: {term['id']})" for term in st.session_state.fetched_terms]
selected_index = st.selectbox("Select a Term", list(range(len(term_names))), format_func=lambda i: term_names[i])
if selected_index == 0:
    st.stop()

selected_term = st.session_state.fetched_terms[selected_index - 1]
st.session_state.selected_term_id = selected_term['id']

# ✅ Use a trigger flag instead of immediate rerun
if st.button("🔍 Search Courses in Selected Term"):
    st.session_state["courses_search_triggered_for_term"] = True
    st.session_state["force_refetch"] = True
    st.rerun()  # Now safely re-runs on next render

# --- Fetch and Filter Courses ---
url = f"{base_url}/api/v1/accounts/{account_id}/courses?enrollment_term_id={selected_term['id']}&per_page=100"
with st.spinner("Fetching courses for selected term..."):
    if "fetched_courses" not in st.session_state or st.session_state.get("force_refetch", False):
        all_courses = _paginated_get_from_api(url, headers)
        st.session_state["fetched_courses"] = all_courses
        st.session_state["force_refetch"] = False
    else:
        all_courses = st.session_state["fetched_courses"]



filtered_courses = []
for course in all_courses:
    restrict = course.get("restrict_enrollments_to_course_dates", False)
    start = course.get("start_at")
    end = course.get("end_at")

    start_blank = not start or start.strip() == ""
    end_blank = not end or end.strip() == ""
    has_start_no_end = start and end_blank
    has_end_no_start = end and start_blank
    has_partial_date = has_start_no_end or has_end_no_start

    now = datetime.utcnow()
    is_currently_active = False

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ") if start else None
        end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ") if end else None
        if start_dt and end_dt:
            is_currently_active = start_dt <= now <= end_dt
    except Exception:
        pass

    if restrict and (has_partial_date or is_currently_active):
        enrollment_count = get_enrollment_count(course['id'], base_url, headers)
        if enrollment_count > 0:
            course["_active_enrollments"] = enrollment_count
            course["_term"] = selected_term['name']
            course["_participation"] = "Date Driven"
            filtered_courses.append(course)

# --- Initialize collapse state for Step 2 ---
if "courses_collapsed" not in st.session_state:
    st.session_state.courses_collapsed = False

def participation_settings_ui(key_prefix="bulk_"):
    with st.expander("📋 Participation Settings for Selected Courses", expanded=True):
        mode = st.radio("Participation Mode", ["Term Driven", "Date Driven"], key=f"{key_prefix}mode")
        start_date, end_date = None, None

        if mode == "Date Driven":
            start_date = st.date_input("Start Date", key=f"{key_prefix}start")
            if st.checkbox("No End Date", key=f"{key_prefix}no_end"):
                end_date = None
            else:
                end_date = st.date_input("End Date", key=f"{key_prefix}end")

        return {
            "mode": mode,
            "start_date": start_date,
            "end_date": end_date
        }

def apply_participation_settings(base_url, selected_courses, headers):
    updated = []
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
            updated.append(course['course_id'])
        except Exception as e:
            st.error(f"❌ Failed to update course {course['course_id']}: {e}")
        progress.progress((i + 1) / total)

    st.success("🎉 All selected courses have been processed.")
    return updated  # ✅ return the list instead of setting session state

    # Save summary info in session state
    st.session_state["last_updates"] = updated
    st.success("🎉 All selected courses have been processed.")

# --- Trigger apply logic if Apply was pressed in last run ---
if st.session_state.get("trigger_apply", False):
    payload = st.session_state["settings_payload"]
    updated_ids = apply_participation_settings(base_url, payload["selected_courses"], headers)

    # 📝 Prepare entries for the CSV audit log
    log_entries = []
    for cid in updated_ids:
        course = next((c for c in filtered_courses if str(c["id"]) == cid), None)
        if course:
            log_entries.append({
                "Course ID": cid,
                "Course Name": course["name"],
                "Participation Mode": payload["selected_mode"],
                "Start Date": course.get("start_at", ""),
                "End Date": course.get("end_at", ""),
                "Updated At": datetime.utcnow().isoformat() + "Z",
                "Changed By": user_email or "Unknown"  # 👈 Logs the email entered earlier
            })

    if log_entries:
        log_course_changes_to_csv(log_entries)

    st.session_state["last_updates"] = updated_ids
    st.session_state["bulk_mode"] = payload["selected_mode"]
    st.session_state["courses_collapsed"] = True
    st.session_state["trigger_apply"] = False
    st.rerun()


# --- Step 2: Course Selection ---
if filtered_courses:
    with st.expander("📘 Step 2: Select and Apply Participation Settings", expanded=not st.session_state.courses_collapsed):
        st.success(f"✅ {len(filtered_courses)} courses with mismatched dates and active enrollments found.")

        col_select_all, col_deselect_all = st.columns(2)
        with col_select_all:
            select_all = st.checkbox("Select All Courses")
        with col_deselect_all:
            deselect_all = st.checkbox("Deselect All Courses")

# --- Course List Display ---
selected_course_ids = []

for course in filtered_courses:
    course_id = str(course['id'])

    if deselect_all:
        st.session_state[f"select_{course_id}"] = False
    elif select_all:
        st.session_state[f"select_{course_id}"] = True

    col1, col2 = st.columns([0.05, 0.95])
    with col1:
        st.checkbox("", key=f"select_{course_id}")
    with col2:
        toggle_key = f"expand_{course_id}"
        expanded = st.toggle(f"📘 {course['name']} (ID: {course_id})", key=toggle_key)
        if expanded:
            st.markdown(f"- **Active Student Enrollments:** {course['_active_enrollments']}")
            st.markdown(f"- **Term:** {course['_term']}")
            st.markdown(f"- **Participation Mode:** {course['_participation']}")
            st.markdown(f"- **Start Date:** {course.get('start_at', 'None')}")
            st.markdown(f"- **End Date:** {course.get('end_at', 'None')}")
            canvas_link = f"https://{canvas_domain}/courses/{course['id']}"
            st.markdown(f"- [Open in Canvas]({canvas_link})")

    if st.session_state.get(f"select_{course_id}", False):
        selected_course_ids.append(course_id)

# ✅ Now outside the loop — only runs once
if selected_course_ids:
    settings = participation_settings_ui()

    if st.button("Apply Settings to Selected Courses"):
        st.session_state["trigger_apply"] = True
        st.session_state["settings_payload"] = {
            "selected_courses": [{
                "course_id": course_id,
                "mode": settings["mode"],
                "start_date": settings["start_date"],
                "end_date": settings["end_date"]
            } for course_id in selected_course_ids],
            "selected_mode": settings["mode"]
        }
        st.rerun()
else:
    st.info("Select at least one course to update.")

# --- Summary of Recently Updated Courses ---
if "last_updates" in st.session_state and st.session_state["last_updates"]:
    st.markdown("### ✅ Recently Updated Courses")
    for cid in st.session_state["last_updates"]:
        course = next((c for c in filtered_courses if str(c["id"]) == cid), None)
        if course:
            st.markdown(f"- 📘 **{course['name']} (ID: {cid})** — {st.session_state.get('bulk_mode', 'Date/Term')} mode applied")
