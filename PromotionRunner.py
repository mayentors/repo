import base64
from email.message import EmailMessage
import json
import os
import time
from unittest.mock import patch, mock_open

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# =====================================================================
# --- VIRTUAL FILE SYSTEM INJECTION FOR STREAMLIT SECRETS ---
# =====================================================================

# 1. Pull real dictionaries from Streamlit Cloud Secrets Dashboard
VIRTUAL_CREDENTIALS_JSON = json.dumps({"web": dict(st.secrets["google_creds"])})
VIRTUAL_TOKEN_JSON = json.dumps(dict(st.secrets["google_token"]))

# 2. Store original system file checks before we modify them
original_exists = os.path.exists
original_isfile = os.path.isfile
original_open = open

def virtual_exists(path):
    if os.path.basename(path) in ["credentials.json", "token.json"]:
        return True
    return original_exists(path)

def virtual_isfile(path):
    if os.path.basename(path) in ["credentials.json", "token.json"]:
        return True
    return original_isfile(path)

def virtual_open(file, mode='r', *args, **kwargs):
    filename = os.path.basename(file)
    if filename == "credentials.json":
        return mock_open(read_data=VIRTUAL_CREDENTIALS_JSON)().clone_for_read() if hasattr(mock_open(), 'clone_for_read') else mock_open(read_data=VIRTUAL_CREDENTIALS_JSON)()
    elif filename == "token.json":
        return mock_open(read_data=VIRTUAL_TOKEN_JSON)().clone_for_read() if hasattr(mock_open(), 'clone_for_read') else mock_open(read_data=VIRTUAL_TOKEN_JSON)()
    return original_open(file, mode, *args, **kwargs)

# 3. Inject our virtual environment hooks globally into the running application
os.path.exists = virtual_exists
os.path.isfile = virtual_isfile

# We patch builtins.open so all downstream lines read our virtual secret data automatically
builtins_open_patcher = patch("builtins.open", virtual_open)
builtins_open_patcher.start()

# 4. Standard Token setup using our virtual layout
credentials = Credentials.from_authorized_user_info(dict(st.secrets["google_token"]))

# =====================================================================
# --- BACK TO YOUR ORIGINAL APP CODE ---
# =====================================================================
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

# --- INITIALIZE SESSION STATES ---
if "editor_content" not in st.session_state:
    st.session_state.editor_content = (
        "Subject: Global Introduction Update<br><br>"
        "Dear {Name},<br><br>"
        "Type your core promotional body structure layout text here..."
    )

# --- BACKEND FUNCTIONS ---


def get_gmail_service():
    creds = None
    token_path = os.path.join(SCRIPT_DIR, "token.json")
    credentials_path = os.path.join(SCRIPT_DIR, "credentials.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                st.error(f"❌ `credentials.json` missing at {credentials_path}.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def get_or_create_label(service, label_name):
    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        for label in labels:
            if label["name"].lower() == label_name.lower():
                return label["id"]

        label_object = {
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created_label = (
            service.users()
            .labels()
            .create(userId="me", body=label_object)
            .execute()
        )
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
                personalized_content = personalized_content.replace(
                    placeholder, value
                )

        name_part = str(row.get("Name", f"Record_{index+1}")).strip()
        email_part = str(row.get("Email", "")).strip()
        clean_name = "".join(
            c for c in name_part if c.isalnum() or c in (" ", "_", "-")
        ).rstrip()
        filename = (
            f"{clean_name}_{email_part}.html"
            if email_part
            else f"{clean_name}.html"
        )
        file_path = os.path.join(OUTPUT_FOLDER, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(personalized_content)
        generated_files.append(file_path)
    return generated_files


# --- APPLICATION USER INTERFACE ---

st.title("📧 Automated Mailer & Operations Pipeline")
st.markdown(
    "Upload your demographics matrix and template files to manage your operations step-by-step."
)

col1, col2 = st.columns([1, 1])

# --- STEP 1: MULTI FILE UPLOADER ---
with col1:
    st.subheader("📋 Step 1: Upload Files")
    uploaded_files = st.file_uploader(
        "Upload contacts.txt and template.txt here simultaneously",
        type=["txt", "csv"],
        accept_multiple_files=True,
    )

    df_contacts = None
    template_file_content = None

    # Process files if uploaded
    if uploaded_files:
        for file in uploaded_files:
            if "contacts" in file.name.lower():
                try:
                    df_contacts = pd.read_csv(file)
                    df_contacts.columns = (
                        df_contacts.columns.astype(str).str.strip()
                    )
                    st.success(
                        f"✅ Loaded contacts: Found {len(df_contacts)} records."
                    )
                except Exception as e:
                    st.error(f"Error parsing contacts file: {e}")
            elif "template" in file.name.lower():
                try:
                    raw_bytes = file.read()
                    template_file_content = raw_bytes.decode("utf-8")

                    # Convert plaintext layout linebreaks to HTML breaks for the editor view
                    if not (
                        "<p>" in template_file_content
                        or "<br>" in template_file_content
                    ):
                        template_file_content = template_file_content.replace(
                            "\n", "<br>"
                        )

                    # Update session state with the uploaded file contents
                    st.session_state.editor_content = template_file_content
                    st.success("✅ Loaded template layout successfully.")
                except Exception as e:
                    st.error(f"Error reading template file: {e}")

# --- STEP 2: WYSIWYG TINYMCE EMAIL DRAFTER ---
with col2:
    st.subheader("📝 Step 2: Master Email Content Compositor")
    st.markdown(
        "<small>Modify layout styles, add hyperlinks, or bold elements. Placeholders like <code>{Name}</code> will be preserved.</small>",
        unsafe_allow_html=True,
    )

    # TinyMCE HTML Component String
    tinymce_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://cdn.jsdelivr.net/npm/tinymce@6/tinymce.min.js" referrerpolicy="origin"></script>
    </head>
    <body style="margin:0; padding:0;">
        <textarea id="editor">{st.session_state.editor_content}</textarea>
        <script>
            tinymce.init({{
                selector: '#editor',
                height: 310,
                plugins: 'link image lists table code wordcount',
                toolbar: 'undo redo | blocks | bold italic underline forecolor backcolor | alignleft aligncenter alignright alignjustify | bullist numlist | link image | removeformat',
                branding: false,
                promotion: false,
                setup: function (editor) {{
                    // Instantly push changes up to Streamlit frame container
                    editor.on('change keyup', function () {{
                        window.parent.postMessage({{
                            type: 'streamlit:setComponentValue',
                            value: editor.getContent()
                        }}, '*');
                    }});
                    editor.on('init', function() {{
                        window.parent.postMessage({{
                            type: 'streamlit:setComponentValue',
                            value: editor.getContent()
                        }}, '*');
                    }});
                }}
            }});
        </script>
    </body>
    </html>
    """

    # Render HTML component iframe
    editor_response = components.html(tinymce_html, height=360, scrolling=False)

    # CRITICAL FIX: Update session state persistently ONLY if a valid string is returned
    if editor_response and isinstance(editor_response, str) and editor_response.strip() != "":
        st.session_state.editor_content = editor_response

# Only activate dashboard pipeline controls if contacts data is uploaded successfully
if df_contacts is not None:
    st.markdown("---")

    # Action Dashboard Grid Columns
    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        st.subheader("⚙️ Step 3: Drafts")
        generate_clicked = st.button("🚀 Generate Drafts", use_container_width=True)

    with btn_col2:
        st.subheader("🏷️ Step 4: Approval")
        approve_clicked = st.button("✅ Approve All Drafts", use_container_width=True)

    with btn_col3:
        st.subheader("📤 Step 5: Send")
        send_clicked = st.button("✉️ Send Mails", use_container_width=True)

    pipeline_status = st.container()

    # --- STEP 3 LOGIC: GENERATE DRAFTS ---
    if generate_clicked:
        with pipeline_status:
            status_box = st.status("Running Draft Generation Pipeline...", expanded=True)
            with status_box:
                st.write("🔄 Compiling contact matrix dataset placeholders...")
                
                # Fetch clean, immutable raw template layout string out of memory cache safely
                master_template_string = str(st.session_state.editor_content)
                
                compiled_files = run_compiler_pipeline(df_contacts, master_template_string)
                st.write(f"✔️ Local letter generation complete. Formatted {len(compiled_files)} letters.")

                st.write("🔄 Connecting to Google Mailbox API Services...")
                service = get_gmail_service()
                
                if not service:
                    st.error("❌ Google Workspace connection failed. Check your credentials token.")
                else:
                    label_id = get_or_create_label(service, TARGET_LABEL_NAME)
                    success_drafts = 0

                    for file_path in compiled_files:
                        filename = os.path.basename(file_path)
                        name_email_part = os.path.splitext(filename)[0]
                        to_email = name_email_part.split("_")[-1] if "_" in name_email_part else ""
                        
                        if not to_email or "@" not in to_email:
                            st.warning(f"⚠️ Skipped file '{filename}': Could not extract valid destination email target.")
                            continue

                        with open(file_path, "r", encoding="utf-8") as f:
                            body_content = f.read()

                        # Default backup placeholders
                        subject = "Exclusive Campaign Update"
                        html_body = body_content

                        # Parse Subject Header out of rich component markup strings cleanly
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
                                    
                                html_body = after_subject[end_pos:].strip()
                                while html_body.startswith(("<br>", "<br />", "</p>", "\n")):
                                    html_body = html_body.replace("<br>","",1).replace("<br />","",1).replace("</p>","",1).strip()
                                
                                if parts[0].strip().endswith("<p>") and not html_body.startswith("<p>"):
                                    html_body = "<p>" + html_body
                            except Exception:
                                html_body = body_content

                        # Execute official structural MIME creation array
                        try:
                            message = EmailMessage()
                            message["To"] = to_email
                            message["Subject"] = subject
                            message.set_content(html_body, subtype="html")
                            
                            encoded_bytes = base64.urlsafe_b64encode(message.as_bytes()).decode()
                            draft_payload = {"message": {"raw": encoded_bytes}}
                            
                            # Create Draft Envelope Node
                            draft = service.users().drafts().create(userId="me", body=draft_payload).execute()

                            # Inject structural user validation approval tags directly onto the message ID element
                            if label_id:
                                service.users().messages().modify(
                                    userId="me",
                                    id=draft["message"]["id"],
                                    body={"addLabelIds": [label_id]}
                                ).execute()
                                
                            success_drafts += 1
                            st.write(f"📡 Uploaded draft to your mailbox for: **{to_email}**")
                        except Exception as ex:
                            st.error(f"❌ API Rejected upload for {to_email}: {ex}")

                    status_box.update(label="🎉 Draft Generation Stage Complete!", state="complete")
                    st.success(f"Successfully generated and injected {success_drafts} drafts into your Gmail inbox tagged under `{TARGET_LABEL_NAME}`!")
# --- STEP 4 LOGIC: APPROVE DRAFTS ---
    if approve_clicked:
        with pipeline_status:
            status_box = st.status("Executing Bulk Label Approval Step...")
            with status_box:
                service = get_gmail_service()
                if service:
                    needs_approval_id = get_or_create_label(
                        service, TARGET_LABEL_NAME
                    )
                    approved_id = get_or_create_label(
                        service, APPROVED_LABEL_NAME
                    )

                    st.write("📥 Scanning unapproved drafts inventory...")
                    drafts_response = (
                        service.users().drafts().list(userId="me").execute()
                    )
                    current_drafts = drafts_response.get("drafts", [])

                    approved_count = 0
                    for d in current_drafts:
                        try:
                            detail = (
                                service.users()
                                .drafts()
                                .get(userId="me", id=d["id"], format="full")
                                .execute()
                            )
                            labels = (
                                detail.get("message", {}).get("labelIds", [])
                            )

                            if needs_approval_id in labels:
                                msg_id = detail["message"]["id"]

                                # CRITICAL FIX: Only ADD the approved label, do NOT include removeLabelIds
                                service.users().messages().modify(
                                    userId="me",
                                    id=msg_id,
                                    body={"addLabelIds": [approved_id]},
                                ).execute()
                                approved_count += 1
                                st.write(
                                    f"🏷️ Appended approval tag to draft ID: {d['id']}"
                                )
                        except Exception:
                            pass

                    status_box.update(
                        label="✔️ Approval Processing Loop Complete!",
                        state="complete",
                    )
                    st.success(
                        f"Approval Complete! Added `{APPROVED_LABEL_NAME}` to {approved_count} drafts while successfully retaining the `{TARGET_LABEL_NAME}` tag."
                    )
    # --- STEP 5 LOGIC: SEND MAILS ---
    if send_clicked:
        with pipeline_status:
            status_box = st.status("Initializing Final Outbound Dispatch...")
            with status_box:
                service = get_gmail_service()
                if service:
                    approved_id = get_or_create_label(
                        service, APPROVED_LABEL_NAME
                    )
                    st.write("Scanning for approved items ready to deploy...")

                    drafts_response = (
                        service.users().drafts().list(userId="me").execute()
                    )
                    current_drafts = drafts_response.get("drafts", [])

                    dispatched_count = 0
                    for d in current_drafts:
                        try:
                            detail = (
                                service.users()
                                .drafts()
                                .get(userId="me", id=d["id"], format="full")
                                .execute()
                            )
                            labels = (
                                detail.get("message", {}).get("labelIds", [])
                            )

                            if approved_id in labels:
                                service.users().drafts().send(
                                    userId="me", body={"id": d["id"]}
                                ).execute()
                                dispatched_count += 1
                        except Exception:
                            pass

                    status_box.update(
                        label="🚀 Campaign Dispatch Chain Complete!",
                        state="complete",
                    )
                    if dispatched_count > 0:
                        st.success(
                            f"🎉 Success! {dispatched_count} approved emails have been launched into production pipelines."
                        )
                    else:
                        st.info(
                            f"No remaining messages found containing the `{APPROVED_LABEL_NAME}` execution label to send."
                        )
else:
    st.info("💡 Upload data files and provide body layouts above to unlock execution pipeline triggers.")