import hashlib
import tempfile
from pathlib import Path
import streamlit as st
import sys
from pathlib import Path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from backend.src.case_intake.implementations.case_intake_orchestrator import execute_case_intake_stage
from backend.src.utilities.document_parser import convert_docx_to_pdf_libreoffice
from components.gds_service_toolkit_ui import apply_gds_styles, render_phase_banner, render_caption


st.set_page_config(page_title="Case Intake", layout="wide")
apply_gds_styles()
render_phase_banner("Prototype", "This is an AI Triage PoC")
render_caption("Defra, Natural England internal tooling")

st.title("Case Intake")

st.subheader("Text Inputs")
text_inputs_left, text_inputs_right = st.columns(2)

with text_inputs_left:
    consultation_id = st.text_input("Consultation ID *").strip()
    folder_prefix = st.text_input("Folder prefix", value="test").strip() or "test"

with text_inputs_right:
    bucket = st.text_input("Bucket", value="ai-triage-test").strip() or "ai-triage-test"

st.subheader("Uploads")
uploads_left, uploads_right = st.columns(2)

with uploads_left:
    email = st.file_uploader("Email *", type=["eml", "msg"])
    checklist = st.file_uploader("Hub checklist *", type=["docx"])

with uploads_right:
    model_config = st.file_uploader("Model configuration *", type=["yaml", "yml"])
    prompt = st.file_uploader("Extraction prompt *", type=["md"])

st.subheader("Options")
overwrite = st.checkbox("Replace existing extraction")
run = st.button("Run extraction", type="primary")

st.divider()

uploads = (email, checklist, model_config, prompt)
signature = None

if all(upload is not None for upload in uploads):
    signature = (
        bucket,
        folder_prefix,
        consultation_id,
        tuple(
            (upload.name, hashlib.sha256(upload.getvalue()).hexdigest())
            for upload in uploads
        ),
    )

if run:
    st.session_state.pop("case_intake_result", None)

    if not consultation_id:
        st.error("Enter a consultation ID.")
    elif "/" in consultation_id or "\\" in consultation_id:
        st.error("Consultation ID must be a single folder name.")
    elif not all(upload is not None for upload in uploads):
        st.error("Upload an email, checklist, model configuration and prompt.")
    else:
        try:
            with st.spinner("Processing consultation..."):
                with tempfile.TemporaryDirectory() as directory:
                    temp_path = Path(directory)

                    email_path = temp_path / Path(email.name).name
                    checklist_path = temp_path / "checklist.docx"
                    config_path = temp_path / "model.yaml"
                    prompt_path = temp_path / "prompt.md"

                    email_path.write_bytes(email.getvalue())
                    checklist_path.write_bytes(checklist.getvalue())
                    config_path.write_bytes(model_config.getvalue())
                    prompt_path.write_bytes(prompt.getvalue())

                    pdf_path = convert_docx_to_pdf_libreoffice(
                        checklist_path,
                        temp_path / "checklist_pdf",
                    )
                    checklist_pdf = pdf_path.read_bytes()

                    result = execute_case_intake_stage(
                        email_file_path=email_path,
                        bucket=bucket,
                        folder_prefix=folder_prefix,
                        consultation_id=consultation_id,
                        model_config_path=config_path,
                        prompt_path=prompt_path,
                        overwrite=overwrite,
                    )

            st.session_state["case_intake_result"] = {
                "signature": signature,
                "pdf": checklist_pdf,
                "result": result,
            }
        except Exception as error:
            st.error(f"Processing failed: {error}")

saved = st.session_state.get("case_intake_result")
left, right = st.columns(2)

if saved and saved["signature"] == signature:
    with left:
        st.subheader("Hub checklist")
        st.pdf(saved["pdf"], height=800)

    with right:
        st.subheader("Email extraction")
        st.json(saved["result"]["email_extraction"])