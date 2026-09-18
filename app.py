import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# Page Configuration
st.set_page_config(
    page_title="General Gathering Management Portal",
    page_icon="📅",
    layout="wide"
)

# ---------------------------------------------------------
# 1. AUTHENTICATION (SIMPLE HARDCODED FOR OVERSEER & ASSISTANT)
# ---------------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_role = None

def login():
    st.sidebar.title("🔒 Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    
    if st.sidebar.button("Login"):
        if username == "overseer" and password == "pass123":
            st.session_state.authenticated = True
            st.session_state.user_role = "Overseer"
            st.sidebar.success("Logged in as General Overseer")
            st.rerun()
        elif username == "assistant" and password == "pass123":
            st.session_state.authenticated = True
            st.session_state.user_role = "Assistant Overseer"
            st.sidebar.success("Logged in as Assistant Overseer")
            st.rerun()
        else:
            st.sidebar.error("Invalid username or password")

if not st.session_state.authenticated:
    st.title("📅 General Gathering Management Portal")
    st.info("Please log in using the sidebar to access the portal.")
    login()
    st.stop()

# ---------------------------------------------------------
# 2. GLOBAL EVENT CONFIGURATION & DEPARTMENTS
# ---------------------------------------------------------
st.sidebar.write(f"Logged in: **{st.session_state.user_role}**")
if st.sidebar.button("Logout"):
    st.session_state.authenticated = False
    st.rerun()

st.sidebar.divider()

overseer_depts = ["Accounts", "Attendant", "Cleaning", "First Aid", "Parking", "Rooming (if needed)"]
assistant_depts = ["Audio/Video", "Baptism", "Installation (if needed)", "Lost & Found", "Checkroom"]

# ---------------------------------------------------------
# 3. HEADER & DATE SETUP
# ---------------------------------------------------------
st.title("📅 General Gathering Management Portal")

col1, col2, col3 = st.columns(3)
with col1:
    event_part = st.selectbox("Gathering Part", ["General Gathering Part 1", "General Gathering Part 2"])
with col2:
    event_year = st.number_input("Year", value=datetime.now().year, step=1)
with col3:
    event_date = st.date_input("Confirmed Gathering Date", value=datetime.now() + timedelta(days=60))

# Calculate 4-week recruitment deadline
recruitment_deadline = event_date - timedelta(weeks=4)
days_until_event = (event_date - datetime.now().date()).days
days_until_deadline = (recruitment_deadline - datetime.now().date()).days

# ---------------------------------------------------------
# 4. ALERTS & REMINDERS DASHBOARD
# ---------------------------------------------------------
st.divider()
st.subheader("🔔 Deadline & Task Dashboard")

col_a, col_b, col_c = st.columns(3)
col_a.metric("Days Until Event", f"{days_until_event} days", f"Date: {event_date}")
col_b.metric("Volunteer Recruitment Deadline", f"{recruitment_deadline}", f"{days_until_deadline} days remaining")

if days_until_deadline < 0:
    col_c.error("⚠️ Recruitment Deadline Passed! Verify all 14 department lists.")
elif days_until_deadline <= 7:
    col_c.warning("⚠️ Urgent: Recruitment deadline is in less than a week!")
else:
    col_c.success("✅ Recruitment deadline on track.")

# ---------------------------------------------------------
# 5. NAVIGATION TABS
# ---------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "📋 Pre-Gathering Checklists",
    "🏢 Department Oversight & Meetings",
    "👥 Volunteer Master List & Shift Rotation",
    "📄 Document Generator & Export"
])

# TAB 1: PRE-GATHERING CHECKLISTS
with tab1:
    st.header("Pre-Gathering Verification")
    st.caption("Confirm all general arrangements and requirements prior to the pre-gathering.")

    col_chk1, col_chk2 = st.columns(2)
    
    with col_chk1:
        st.subheader("Overseer Verification")
        st.checkbox("Informed department overseers of limited work setup (A/V, testing, setup)")
        st.checkbox("Confirmed setup work is well-planned and organized")
        st.checkbox("Confirmed volunteers are instructed to bring their own food")
        st.checkbox("Confirmed volunteers are instructed to bring PPE for assigned tasks")
        st.checkbox("Encouraged modest dress and grooming for all volunteers")
        st.checkbox("Confirmed all assigned department overseers completed Job Hazard forms")

    with col_chk2:
        st.subheader("Assistant Overseer Verification")
        st.checkbox("Assistant's assigned departments briefed on setup & testing")
        st.checkbox("Assistant's assigned departments briefed on food & PPE requirements")
        st.checkbox("Assistant's assigned departments completed Job Hazard forms")
        st.checkbox("Confirmed recruitment complete 4 weeks prior to event across all departments")

# TAB 2: DEPARTMENT OVERSIGHT & MEETINGS
with tab2:
    st.header("Department Oversight & Instructions")
    
    selected_dept = st.selectbox("Select Department to Manage", overseer_depts + assistant_depts)
    
    st.subheader(f"Management Details for: {selected_dept}")
    
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.text_input("Department Overseer Name")
        st.text_input("Assistant Overseer Name")
        st.text_input("Keymen Names")
    
    with col_d2:
        st.date_input(f"Scheduled Pre-Gathering Meeting Date for {selected_dept}")
        st.checkbox("Gathering Overseer plans to attend this meeting")
        st.checkbox("Volunteer training date arranged and confirmed")
        st.checkbox("Job Hazard Form submitted and verified")

# TAB 3: VOLUNTEER MASTER LIST & SHIFT ROTATION
with tab3:
    st.header("Volunteer Master List & Shift Scheduling")
    st.info("Ensure volunteers working in the morning shift DO NOT work in the afternoon shift so everyone can enjoy the program.")

    # Sample Data Structure
    if "volunteers" not in st.session_state:
        st.session_state.volunteers = pd.DataFrame([
            {"Name": "John Doe", "Department": "Attendant", "Shift": "Morning", "Status": "Active", "Contact": "555-0101"},
            {"Name": "Jane Smith", "Department": "Attendant", "Shift": "Afternoon", "Status": "Active", "Contact": "555-0102"},
            {"Name": "Mark Johnson", "Department": "Audio/Video", "Shift": "Morning", "Status": "Available", "Contact": "555-0103"},
            {"Name": "Sarah Lee", "Department": "Cleaning", "Shift": "Afternoon", "Status": "Not Available", "Contact": "555-0104"},
        ])

    st.subheader("Add / Update Volunteer Details")
    edited_df = st.data_editor(
        st.session_state.volunteers,
        num_rows="dynamic",
        column_config={
            "Shift": st.column_config.SelectboxColumn("Shift Assignment", options=["Morning", "Afternoon"]),
            "Status": st.column_config.SelectboxColumn("Availability Status", options=["Active", "Available", "Not Available", "Moved Out"]),
            "Department": st.column_config.SelectboxColumn("Department", options=overseer_depts + assistant_depts)
        },
        use_container_width=True
    )
    st.session_state.volunteers = edited_df

# TAB 4: DOCUMENT GENERATOR
with tab4:
    st.header("Generate Documents & Reports")
    st.write("Generate consolidated master schedules or export current volunteer allocations.")

    st.download_button(
        label="📥 Export Master Volunteer List (CSV)",
        data=st.session_state.volunteers.to_csv(index=False),
        file_name=f"{event_part}_{event_year}_Volunteer_Master_List.csv",
        mime="text/csv"
    )
