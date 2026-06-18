import base64
from email.message import EmailMessage
import json
import os
import time
import pandas as pd
import streamlit as st
from streamlit_quill import st_quill
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# --- INITIAL SETTINGS ---
st.set_page_config(page_title="Automated Marketing Mailer Engine", layout="wide")

# --- LOGIN GATEWAY ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.subheader("🔒 Administrative Access Required")
    input_user = st.text_input("Username")
    input_pass = st.text_input("Password", type="password")
    
    if st.button("Login"):
        if input_user == st.secrets["auth"]["username"] and input_pass == st.secrets["auth"]["password"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop() # 🛑 CRITICAL: This kills code execution for unauthenticated public traffic
# --- API SCOPES & CONSTANTS ---
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
TARGET_LABEL_NAME = "AI_Generated_Needs_Human_Approval"
APPROVED_LABEL_NAME = "Manually_Approved"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "personalized_letters")

st.set_page_config(
    page_title="Automated Marketing Mailer Engine",
    page_icon="📧",
    layout="wide",
)

if not os.path.exists(OUTPUT_FOLDER):
    os.makedirs(OUTPUT_FOLDER)

# --- TRACK EXPLICIT SESSION STATE ---
if "editor_markup" not in st.session_state:
    st.session_state.editor_markup = (
        "<p><strong>Subject: Global Introduction Update</strong></p>"
        "<p>Dear {Name},</p>"
        "<p>Type your core promotional body structure layout text here...</p>"
    )

if "last_loaded_file" not in st.session_state:
    st.session_state.last_loaded_file = "default"

# --- BACKEND FUNCTIONS ---

def get_gmail_service():
    """Checks local environment files first for debugging, falls back to Cloud Secrets."""
    local_token = os.path.join(SCRIPT_DIR, "token.json")
    
    if os.path.exists(local_token):
        try:
            creds = Credentials.from_authorized_user_file(local_token, SCOPES)
            if creds and creds.valid:
                return build('gmail', 'v1', credentials=creds)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                return build('gmail', 'v1', credentials=creds)
        except Exception:
            pass

    if "google_token" in st.secrets:
        token_info = dict(st.secrets["google_token"])
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build('gmail', 'v1', credentials=creds)
    return None

def get_or_create_label(service, label_name):
    """Fetches or builds system labels without Cloud ID collision mutations."""
    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        
        for label in labels:
            if label["name"].strip().lower() == label_name.strip().lower():
                return label["id"]
        
        label_object = {
            "name": label_name, 
            "labelListVisibility": "labelShow", 
            "messageListVisibility": "show"
        }
        created_label = service.users().labels().create(userId="me", body=label_object).execute()
        return created_label["id"]
    except Exception:
        return None

def run_compiler_pipeline(df, html_template):
    for f in os.listdir(OUTPUT_FOLDER):
        if f.endswith(".html"):
            os.remove(os.path.join(OUTPUT_FOLDER, f))

    generated_files = []
    for index, row in df.iterrows():
        personalized_content = html_template
        for column_name in df.columns:
            placeholder = f"{{{column_name}}}"
            value = str(row[column_name]).strip()
            if placeholder in personalized_content:
                personalized_content = personalized_content.replace(placeholder, value)

        name_part = str(row.get("Name", f"Record_{index+1}")).strip()
        email_part = str(row.get("Email", "")).strip()
        clean_name = "".join(c for c in name_part if c.isalnum() or c in (" ", "_", "-")).rstrip()
        filename = f"{clean_name}_{email_part}.html" if email_part else f"{clean_name}.html"
        file_path = os.path.join(OUTPUT_FOLDER, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(personalized_content)
        generated_files.append(file_path)
    return generated_files


# --- APPLICATION USER INTERFACE ---

st.title("📧 Automated Mailer & Operations Pipeline")
st.markdown("Upload your demographics matrix and template files to manage your operations step-by-step.")

col1, col2 = st.columns([1, 1])

# --- STEP 1: FILE INGESTION MAPPING ---
with col1:
    st.subheader("📋 Step 1: Upload Files")
    uploaded_files = st.file_uploader(
        "Upload contacts.txt and template.txt here simultaneously",
        type=["txt", "csv"],
        accept_multiple_files=True
    )

    df_contacts = None
    if uploaded_files:
        for file in uploaded_files:
            if "contacts" in file.name.lower():
                try:
                    df_contacts = pd.read_csv(file)
                    df_contacts.columns = df_contacts.columns.astype(str).str.strip()
                    st.success(f"✅ Loaded contacts: Found {len(df_contacts)} records.")
                except Exception as e:
                    st.error(f"Error parsing contacts file: {e}")
            elif "template" in file.name.lower():
                try:
                    file_tracking_key = f"loaded_{file.name}_{file.size}"
                    if st.session_state.last_loaded_file != file_tracking_key:
                        raw_bytes = file.read()
                        uploaded_content = raw_bytes.decode("utf-8")
                        
                        if not ("<p>" in uploaded_content or "<br>" in uploaded_content):
                            lines = uploaded_content.split("\n")
                            uploaded_content = "".join([f"<p>{line}</p>" if line.strip() else "<br>" for line in lines])
                        
                        st.session_state.editor_markup = uploaded_content
                        st.session_state.last_loaded_file = file_tracking_key
                        st.success("📝 Template imported successfully into compositor memory!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Error reading template file: {e}")

# --- STEP 2: NATIVE ADVANCED RICH TEXT COMPOSITOR ---
with col2:
    st.subheader("📝 Step 2: Master Email Content Compositor")
    st.markdown("<small>Use the options below to format your email. Edits are tracked natively and safely.</small>", unsafe_allow_html=True)

    editor_response = st_quill(
        value=st.session_state.editor_markup,
        html=True,
        placeholder="Type your email body context here...",
        key=f"quill_editor_{st.session_state.last_loaded_file}"
    )

    if editor_response and editor_response != st.session_state.editor_markup:
        st.session_state.editor_markup = editor_response

    # Save tracking block layout
    save_col1, save_col2 = st.columns([3, 1])
    with save_col2:
        if st.button("💾 Save Template Changes", type="primary", use_container_width=True):
            if editor_response:
                st.session_state.editor_markup = editor_response
            st.success("Changes committed to master layout!")

# --- EXECUTION DASHBOARD CONTROLS ---
if df_contacts is not None:
    st.markdown("---")
    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        st.subheader("⚙️ Step 3: Drafts")
        generate_clicked = st.button("🚀 Generate Drafts", use_container_width=True)
    with btn_col2:
        st.subheader("🏷️ Step 4: Approval")
        approve_clicked = st.button("✅ Approve All Drafts", use_container_width=True)
    with btn_col3:
        st.subheader("📤 Step 5: Send")
        send_clicked = st.button(
            "✉️ Send Mails",
            use_container_width=True, 
            disabled=True, 
            help="Outbound campaign dispatch is disabled on public URLs for mailbox security."
        )



    pipeline_status = st.container()

    # ... Keep Steps 3 and 4 logic exactly as they are ...

    # --- STEP 5 LOGIC: SAFELY TRUNCATED ---
    if send_clicked:
        with pipeline_status:
            # Hard backend barrier fallback
            st.error("❌ Outbound transmission blocked. Code execution disabled on public nodes.")

    # --- STEP 3 LOGIC: GENERATE DRAFTS ---
    if generate_clicked:
        with pipeline_status:
            status_box = st.status("Running Draft Generation Pipeline...", expanded=True)
            with status_box:
                st.write("🔄 Fetching stabilized native rich-text state from memory...")
                
                final_template = str(st.session_state.editor_markup)
                compiled_files = run_compiler_pipeline(df_contacts, final_template)
                st.write(f"✔️ Local letter generation complete. Formatted {len(compiled_files)} letters.")

                st.write("🔄 Connecting to Google Mailbox API Services...")
                service = get_gmail_service()
                
                if not service:
                    st.error("❌ Google Workspace connection failed.")
                else:
                    label_id = get_or_create_label(service, TARGET_LABEL_NAME)
                    success_drafts = 0

                    for file_path in compiled_files:
                        filename = os.path.basename(file_path)
                        name_email_part = os.path.splitext(filename)[0]
                        to_email = name_email_part.split("_")[-1] if "_" in name_email_part else ""
                        
                        if not to_email or "@" not in to_email:
                            continue

                        with open(file_path, "r", encoding="utf-8") as f:
                            body_content = f.read()

                        subject = "Exclusive Campaign Update"
                        html_body = body_content

                        # Parse clean structural HTML parameters safely out of rich markup data streams
                        if "Subject:" in body_content:
                            try:
                                parts = body_content.split("Subject:", 1)
                                after_subject = parts[1]
                                end_pos = len(after_subject)
                                for marker in ["</p>", "<br>", "\n"]:
                                    if marker in after_subject:
                                        marker_pos = after_subject.find(marker)
                                        if marker_pos < end_pos:
                                            end_pos = marker_pos
                                            
                                subject = after_subject[:end_pos].replace("\r", "").replace("\n", "").strip()
                                
                                if ">" in subject:
                                    subject = subject.split(">")[-1].strip()
                                for tag in ["<strong>", "</strong>", "<em>", "</em>", "</p>"]:
                                    subject = subject.replace(tag, "")
                                    
                                html_body = after_subject[end_pos:].strip()
                                while html_body.startswith(("<br>", "<br />", "</p>", "\n")):
                                    html_body = html_body.replace("<br>","",1).replace("<br />","",1).replace("</p>","",1).strip()
                                if parts[0].strip().endswith("<p>") and not html_body.startswith("<p>"):
                                    html_body = "<p>" + html_body
                            except Exception:
                                html_body = body_content

                        try:
                            message = EmailMessage()
                            message["To"] = to_email
                            message["Subject"] = subject
                            message.set_content(html_body, subtype="html")
                            
                            encoded_bytes = base64.urlsafe_b64encode(message.as_bytes()).decode()
                            
                            active_labels = ["DRAFT"]
                            if label_id:
                                active_labels.append(label_id)

                            message_payload = {
                                "raw": encoded_bytes,
                                "labelIds": active_labels
                            }
                            
                            service.users().messages().insert(userId="me", body=message_payload).execute()
                                
                            success_drafts += 1
                            st.write(f"📡 Uploaded draft to your mailbox for: **{to_email}**")
                        except Exception as ex:
                            st.error(f"❌ API Rejected upload for {to_email}: {ex}")

                    status_box.update(label="🎉 Draft Generation Stage Complete!", state="complete")
                    st.success(f"Successfully injected {success_drafts} drafts into your Gmail inbox tagged under `{TARGET_LABEL_NAME}`!")

    # --- STEP 4 LOGIC: APPROVE DRAFTS ---
    if approve_clicked:
        with pipeline_status:
            status_box = st.status("Executing Bulk Label Approval Step...")
            with status_box:
                service = get_gmail_service()
                if service:
                    needs_approval_id = get_or_create_label(service, TARGET_LABEL_NAME)
                    approved_id = get_or_create_label(service, APPROVED_LABEL_NAME)

                    messages_response = service.users().messages().list(userId="me", labelIds=[needs_approval_id]).execute()
                    current_messages = messages_response.get("messages", [])

                    approved_count = 0
                    for m in current_messages:
                        try:
                            service.users().messages().modify(
                                userId="me", 
                                id=m["id"], 
                                body={"addLabelIds": [approved_id]}
                            ).execute()
                            approved_count += 1
                        except Exception:
                            pass
                    status_box.update(label="✔️ Approval Processing Loop Complete!", state="complete")
                    st.success(f"Approval Complete! Added `{APPROVED_LABEL_NAME}` label to {approved_count} matching drafts.")

    # --- STEP 5 LOGIC: SEND MAILS ---
    if send_clicked:
        with pipeline_status:
            status_box = st.status("Initializing Final Outbound Dispatch...")
            with status_box:
                service = get_gmail_service()
                if service:
                    approved_id = get_or_create_label(service, APPROVED_LABEL_NAME)
                    
                    # Look up messages that have been approved AND are still drafts
                    messages_response = service.users().messages().list(userId="me", labelIds=[approved_id, "DRAFT"]).execute()
                    current_messages = messages_response.get("messages", [])

                    dispatched_count = 0
                    for m in current_messages:
                        try:
                            # 1. Fetch the raw message contents using the messages data stream
                            msg_details = service.users().messages().get(userId="me", id=m["id"], format="raw").execute()
                            raw_content = msg_details["raw"]

                            # 2. Transmit the email to the live production network
                            send_payload = {"raw": raw_content}
                            service.users().messages().send(userId="me", body=send_payload).execute()
                            
                            # 3. CRITICAL ACCESS FIX: Move the tracking draft to the TRASH folder.
                            # This utilizes the existing 'gmail.modify' scope, avoiding 403 authorization crashes.
                            service.users().messages().trash(userId="me", id=m["id"]).execute()
                                
                            dispatched_count += 1
                            st.write(f"🚀 Launched email transmission chain for record ID: **{m['id']}**")
                        except Exception as e:
                            st.error(f"❌ Dispatch failed for message context {m['id']}: {e}")
                            
                    status_box.update(label="🚀 Campaign Dispatch Chain Complete!", state="complete")
                    if dispatched_count > 0:
                        st.success(f"🎉 Success! {dispatched_count} approved emails have been launched into production pipelines.")
                    else:
                        st.info("No remaining messages found containing the approval tags ready to send.")
else:
    st.info("💡 Upload data files and provide body layouts above to unlock execution pipeline triggers.")