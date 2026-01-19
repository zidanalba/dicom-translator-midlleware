print("STEP 1: app.py loaded")
from flask import Flask, request, Response, render_template, jsonify, redirect, url_for, make_response
from lxml import etree
from pynetdicom import AE
from pynetdicom.sop_class import ModalityWorklistInformationFind, CTImageStorage
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, SecondaryCaptureImageStorage
import pydicom
from pydicom import dcmread
from xml.etree.ElementTree import Element, SubElement, tostring
import logging
import json
import os
import time
import datetime
import requests
import sys
import threading
from collections import deque
from flask import jsonify
from xml.etree.ElementTree import Element, SubElement, tostring
from pydicom.valuerep import PersonName

LOG_BUFFER = deque(maxlen=300)  # keep last 300 lines

class StreamToBuffer:
    def __init__(self, buffer):
        self.buffer = buffer
        self.lock = threading.Lock()

    def write(self, message):
        if message.strip():
            with self.lock:
                self.buffer.append(message.rstrip())

    def flush(self):
        pass

# sys.stdout = StreamToBuffer(LOG_BUFFER)

app = Flask(__name__)
print("STEP 2: Flask created")

CONFIG_PATH = "config.json"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=4)

config = load_config()

ORDER_CONFIG = config.get("ORDER", {})
UPLOAD_CONFIG = config.get("UPLOAD", {})
DEMO_CONFIG = config.get("DEMO", {})
PATHS_CONFIG = config.get("PATHS", {})

ORDER_AE_TITLE = ORDER_CONFIG["ORDER_AE_TITLE"]
ORDER_IP = ORDER_CONFIG["ORDER_IP"]
ORDER_PORT = ORDER_CONFIG["ORDER_PORT"]
ORDER_API_ADDRESS = ORDER_CONFIG["ORDER_API_ADDRESS"]
LOCAL_AE_TITLE = ORDER_CONFIG["LOCAL_AE_TITLE"]
IS_QUERY_PACS = ORDER_CONFIG["ENABLE_PACS"]
IS_QUERY_BACKEND_SERVICE = ORDER_CONFIG["ENABLE_BACKEND"]

SEND_TO_PACS = UPLOAD_CONFIG["ENABLE_PACS"]
UPLOAD_AE_TITLE = UPLOAD_CONFIG["UPLOAD_AE_TITLE"]
UPLOAD_IP = UPLOAD_CONFIG["UPLOAD_IP"]
UPLOAD_PORT = UPLOAD_CONFIG["UPLOAD_PORT"]
SEND_TO_API = UPLOAD_CONFIG["ENABLE_API"]
HIS_API_URL = UPLOAD_CONFIG["API_URL"]

WORKLIST_FOLDER = DEMO_CONFIG["ORTHANC_WORKLIST_FOLDER"]

UPLOAD_FOLDER = PATHS_CONFIG["RESULT_FOLDER"]
RESULT_FOLDER = PATHS_CONFIG["LOG"]

os.makedirs(UPLOAD_FOLDER, exist_ok=True) 

logging.basicConfig(level=logging.INFO)

def parse_patient_id_from_xml(xml_data):
    root = etree.fromstring(xml_data)
    return root.findtext("PatientID")

def create_cfind_dataset(patient_id):
    ds = Dataset()
    ds.PatientID = patient_id
    ds.PatientName = ''
    ds.PatientSex = ''
    ds.PatientAge = ''
    ds.ScheduledProcedureStepSequence = [Dataset()]
    ds.ScheduledProcedureStepSequence[0].Modality = ''
    ds.ScheduledProcedureStepSequence[0].ScheduledStationAETitle = ''
    ds.ScheduledProcedureStepSequence[0].ScheduledProcedureStepStartDate = ''
    return ds

def dicom_cfind(patient_id):
    ae = AE(ae_title=LOCAL_AE_TITLE)
    ae.add_requested_context(ModalityWorklistInformationFind)

    assoc = ae.associate(ORDER_IP, ORDER_PORT, ae_title=ORDER_AE_TITLE)
    if not assoc.is_established:
        raise ConnectionError("Could not associate with PACS server")

    logging.info(f"Sending C-FIND for PatientID: {patient_id}")
    ds = create_cfind_dataset(patient_id)

    responses = assoc.send_c_find(ds, ModalityWorklistInformationFind)
    result = None
    for (status, identifier) in responses:
        if status:
            if status.Status in [0xFF00, 0xFF01]:  # Pending responses
                logging.info("Received matching worklist item")
                result = identifier
            elif status.Status == 0x0000:
                logging.info("C-FIND completed")
        else:
            logging.warning("C-FIND failed or empty status")

    if result:
        logging.info("----- C-FIND Result Dataset -----")
        logging.info(result)

    assoc.release()
    return result

def xml_response(code, message):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<root>
    <result>
        <Code>{code}</Code>
        <Message>{message}</Message>
    </result>
</root>"""
    response = make_response(xml)
    response.headers['Content-Type'] = 'application/xml'
    return response

def send_dicom_to_orthanc(dicom_path, dest_ae=UPLOAD_AE_TITLE, dest_host=UPLOAD_IP, dest_port=UPLOAD_PORT):
    # Create Application Entity
    ae = AE(ae_title='FLASK')

    # Add requested presentation context (storage SCP)
    ae.add_requested_context(CTImageStorage)

    # Read the DICOM file
    ds = dcmread(dicom_path)

    ae.add_requested_context(ds.SOPClassUID)

    # Associate with Orthanc
    assoc = ae.associate(dest_host, dest_port, ae_title=dest_ae)

    if assoc.is_established:
        print("Association established, sending DICOM file...")
        status = assoc.send_c_store(ds)

        if status:
            print(f"C-STORE request status: 0x{status.Status:04x}")
        else:
            print("Connection timed out or was aborted.")

        assoc.release()
    else:
        print("Could not establish association with Orthanc.")

def format_patient_name(pn):
    if not pn:
        return ""

    if isinstance(pn, PersonName):
        # Join available name parts with spaces
        parts = [
            pn.family_name,
            pn.given_name,
            pn.middle_name,
            pn.name_prefix,
            pn.name_suffix,
        ]
        return " ".join(p for p in parts if p)

    # Fallback for string
    return str(pn).replace("^", " ")

def dicom_to_xml_response(ds: Dataset) -> str:
    root = Element('root')

    # Kode sukses dan pesan
    code = SubElement(root, 'Code')
    code.text = '1'

    message = SubElement(root, 'Message')
    message.text = ''

    # Bagian records -> rows
    records = SubElement(root, 'records')
    rows = SubElement(records, 'rows')

    SubElement(rows, 'SerialNo').text = ds.get('AccessionNumber', '')
    SubElement(rows, 'PatientID').text = ds.get('PatientID', '')
    
    # PatientName bisa berupa PersonName object, kita konversi ke string
    patient_name = ds.get('PatientName', '')
    SubElement(rows, 'PatientName').text = format_patient_name(patient_name)

    SubElement(rows, 'PatientSex').text = ds.get('PatientSex', '')
    SubElement(rows, 'PatientAge').text = ds.get('PatientAge', '')
    SubElement(rows, 'PatientAgeUnit').text = 'Y'  # default hardcoded

    SubElement(rows, 'PatientBirthDate').text = ds.get('PatientBirthDate', '')
    SubElement(rows, 'RequestDepartment').text = ds.get('RequestingPhysician', '')
    SubElement(rows, 'RequestID').text = ds.get('AccessionNumber', '')
    SubElement(rows, 'SickBedNo').text = ds.get('BedNumber', '')
    SubElement(rows, 'Pacemaker').text = '2'  # default: tidak ada pacemaker
    SubElement(rows, 'ExamDepartment').text = ds.get('PerformingPhysicianName', '')
    SubElement(rows, 'Priority').text = ds.get('Priority', '')
    SubElement(rows, 'fileGuid').text = ds.get('AccessionNumber', '')
    SubElement(rows, 'RequestDate').text = ds.get('StudyDate', '')

    # Hasil XML
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding='unicode')

def backend_to_xml_response(data: dict) -> str:
    root = Element("root")

    SubElement(root, "Code").text = "1"
    SubElement(root, "Message").text = ""

    records = SubElement(root, "records")
    rows = SubElement(records, "rows")

    def add(tag, value):
        SubElement(rows, tag).text = "" if value is None else str(value)

    add("SerialNo", data.get("SerialNo"))
    add("PatientID", data.get("PatientID"))
    add("PatientName", data.get("PatientName"))
    add("PatientSex", data.get("PatientSex"))
    add("PatientAge", data.get("PatientAge"))
    add("PatientAgeUnit", "Y")
    add("PatientBirthDate", data.get("PatientBirthDate"))
    add("RequestDepartment", data.get("RequestDepartment"))
    add("RequestID", data.get("RequestID"))
    add("SickBedNo", data.get("SickBedNo"))
    add("Pacemaker", data.get("Pacemaker"))
    add("ExamDepartment", data.get("ExamDepartment"))
    add("Priority", data.get("Priority"))
    add("fileGuid", data.get("fileGuid"))
    add("RequestDate", data.get("RequestDate"))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + tostring(root, encoding="unicode")
    )

def query_backend_service(patient_id: str, api_address: str):
    if not api_address:
        raise ValueError("ORDER_API_ADDRESS is empty")

    try:
        logging.info(f"Query backend service for PatientID={patient_id}")

        response = requests.get(
            api_address,
            params={"patient_id": patient_id},
            timeout=5
        )
        response.raise_for_status()

        data = response.json()

        if not data or data.get("code") != 1:
            logging.info("Backend returned no data")
            return None

        return backend_to_xml_response(data["data"])

    except requests.RequestException as e:
        logging.error(f"Backend service error: {e}")
        return None

def get_worklists_from_folder():
    worklists = []
    for filename in os.listdir(WORKLIST_FOLDER):
        if filename.lower().endswith('.wl'):
            try:
                filepath = os.path.join(WORKLIST_FOLDER, filename)
                ds = pydicom.dcmread(filepath)

                info = {
                    "filename": filename,
                    "PatientName": getattr(ds, "PatientName", "Unknown"),
                    "PatientID": getattr(ds, "PatientID", "Unknown"),
                    "Modality": getattr(ds, "Modality", "Unknown"),
                    "ScheduledDate": getattr(ds, "ScheduledProcedureStepStartDate", "N/A"),
                    "ScheduledTime": getattr(ds, "ScheduledProcedureStepStartTime", "N/A"),
                    "StudyDescription": getattr(ds, "StudyDescription", "N/A"),
                }
                worklists.append(info)
            except Exception as e:
                print(f"Failed to parse {filename}: {e}")
    return worklists

def send_data_to_his(ds, pdf_path=None):
    try:
        data = {
            "patient_id": getattr(ds, "PatientID", ""),
            "patient_name": str(getattr(ds, "PatientName", "")),
            "study_description": getattr(ds, "StudyDescription", ""),
            "modality": getattr(ds, "Modality", ""),
            "accession_number": getattr(ds, "AccessionNumber", ""),
        }

        print("data", data)

        files = {}
        if pdf_path and os.path.exists(pdf_path):
            files["pdf"] = open(pdf_path, "rb")

        # headers = {
        #     "Authorization": f"Bearer {HIS_API_KEY}"
        # }

        response = requests.post(HIS_API_URL, data=data, files=files)

        if response.status_code == 200:
            print("Successfully sent data to HIS")
        else:
            print(f"Failed to send to HIS: {response.status_code}, {response.text}")

    except Exception as e:
        print(f"Error sending to HIS: {e}")

@app.route("/query", methods=["POST"])
def query_worklist():
    try:
        xml_data = request.data
        patient_id = parse_patient_id_from_xml(xml_data)

        logging.info(f"Received request for PatientID: {patient_id}")

        config = load_config()
        order_cfg = config["ORDER"]

        xml_response = None

        if order_cfg["ENABLE_BACKEND"]:
            logging.info("Querying BACKEND service")
            xml_response = query_backend_service(
                patient_id,
                order_cfg.get("ORDER_API_ADDRESS", "")
            )

        elif order_cfg["ENABLE_PACS"]:
            logging.info("Querying PACS (C-FIND)")
            dicom_response = dicom_cfind(patient_id)
            xml_response = dicom_to_xml_response(dicom_response)

        else:
            raise Exception("No query source enabled")

        if not xml_response:
            xml_response = etree.tostring(
                etree.Element("Error", message="Patient not found"),
                pretty_print=True,
                xml_declaration=True,
                encoding="UTF-8"
            )


        return Response(xml_response, mimetype="application/xml")

    except Exception as e:
        logging.exception("Error in query_worklist")
        return Response(
            etree.tostring(
                etree.Element("Error", message=str(e)),
                pretty_print=True,
                xml_declaration=True,
                encoding="UTF-8"
            ),
            mimetype="application/xml",
            status=500
        )


@app.route("/receive", methods=["POST"])
def receive():
    try:
        log_path = os.path.join(RESULT_FOLDER, "request_debug.log")
        filename = None
        file_content = None

        with open(log_path, "a", encoding="utf-8") as log:
            if request.content_type and request.content_type.startswith("multipart/form-data"):
                
                if not request.files:
                    return xml_response(-1, "No file part in multipart upload")

                # Use first file part
                uploaded_file = next(iter(request.files.values()))
                filename = uploaded_file.filename or f"upload_{int(time.time())}.dcm"
                file_content = uploaded_file.read()


            else:
                filename = request.headers.get("Filename") or f"upload_{int(time.time())}.dcm"
                file_content = request.get_data()


        # Save file
        if not file_content:
            return xml_response(-1, "Empty file content")

        safe_filename = os.path.basename(filename)
        save_path = os.path.join(UPLOAD_FOLDER, safe_filename)
        with open(save_path, "wb") as f:
            f.write(file_content)


        # Push to Orthanc
        send_dicom_to_orthanc(save_path)

        # Read DICOM and extract PDF if available
        ds = pydicom.dcmread(save_path)
        pdf_path = None
        if hasattr(ds, "EncapsulatedDocument"):
            pdf_bytes = ds.EncapsulatedDocument
            pdf_filename = safe_filename.replace(".dcm", ".pdf")
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
            with open(pdf_path, "wb") as pdf_file:
                pdf_file.write(pdf_bytes)
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"Extracted PDF saved to: {pdf_path}\n")
        else:
            with open(log_path, "a", encoding="utf-8") as log:
                log.write("No EncapsulatedDocument found in DICOM file\n")

        # Send to HIS if configured
        if SEND_TO_API:
            send_data_to_his(ds, pdf_path)

        return xml_response(1, "Upload successful")

    except Exception as e:
        error_msg = f"Upload Error: {e}"
        with open(os.path.join(UPLOAD_FOLDER, "request_debug.log"), "a", encoding="utf-8") as log:
            log.write(f"{error_msg}\n")
        print(error_msg)
        return xml_response(-1, "Server error")

@app.route("/receive-two", methods=["POST"])
def receive_file():
    try:
        filename = None
        file_content = None

        log_path = os.path.join(UPLOAD_FOLDER, "request_debug.log")
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("\n\n=== New Request ===\n")

            # 🔎 Log request headers
            log.write("=== Incoming Headers ===\n")
            for k, v in request.headers.items():
                log.write(f"{k}: {v}\n")
            log.write("========================\n")

            # 🔎 Log request meta info
            log.write(f"Content-Type: {request.content_type}\n")
            log.write(f"Content-Length: {request.content_length}\n")

            if request.content_type and request.content_type.startswith("multipart/form-data"):
                log.write("=== Multipart Form Debug ===\n")
                log.write(f"Form fields: {request.form.to_dict()}\n")
                log.write(f"File keys: {list(request.files.keys())}\n")

                for key, storage in request.files.items():
                    size = len(storage.read())
                    storage.seek(0)  # reset pointer for saving later
                    log.write(f"File field: {key}\n")
                    log.write(f"  -> filename: {storage.filename}\n")
                    log.write(f"  -> content_type: {storage.content_type}\n")
                    log.write(f"  -> size: {size} bytes\n")
                log.write("===========================\n")

                if request.files:
                    uploaded_file = next(iter(request.files.values()))
                    filename = uploaded_file.filename
                    file_content = uploaded_file.read()
                else:
                    return xml_response(-1, "No file part in multipart upload")

            else:
                filename = request.headers.get("Filename")
                if not filename:
                    return xml_response(-1, "Filename header is missing")
                file_content = request.get_data()

        # ✅ Save the file
        safe_filename = os.path.basename(filename)
        save_path = os.path.join(UPLOAD_FOLDER, safe_filename)

        with open(save_path, "wb") as f:
            f.write(file_content)

        # Log success
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"Received file: {safe_filename}\n")
            log.write(f"Saved to: {save_path}\n")

        # Push to Orthanc
        send_dicom_to_orthanc(save_path)

        # Extract PDF if encapsulated
        ds = pydicom.dcmread(save_path)
        pdf_path = None
        if hasattr(ds, "EncapsulatedDocument"):
            pdf_bytes = ds.EncapsulatedDocument
            pdf_filename = safe_filename.replace(".dcm", ".pdf")
            pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)
            with open(pdf_path, "wb") as pdf_file:
                pdf_file.write(pdf_bytes)

            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"Extracted PDF saved to: {pdf_path}\n")

        if SEND_TO_API:
            send_data_to_his(ds, pdf_path)

        return xml_response(1, "Upload successful")

    except Exception as e:
        with open(os.path.join(UPLOAD_FOLDER, "request_debug.log"), "a", encoding="utf-8") as log:
            log.write(f"Upload Error: {e}\n")
        return xml_response(-1, "Server error")

@app.route("/config", methods=["GET", "POST"])
def edit_config():
    if request.method == "POST":
        query_mode = request.form.get("QUERY_MODE", "pacs")

        updated_config = {
            "MODE": "demo",

            "ORDER": {
                "ENABLE_PACS": query_mode == "pacs",
                "ENABLE_BACKEND": query_mode == "backend",

                "LOCAL_AE_TITLE": request.form["LOCAL_AE_TITLE"],
                "ORDER_AE_TITLE": request.form["ORDER_AE_TITLE"],
                "ORDER_IP": request.form["ORDER_IP"],
                "ORDER_PORT": int(request.form["ORDER_PORT"]),
                "ORDER_API_ADDRESS": request.form.get("ORDER_API_ADDRESS", ""),
            },

            "UPLOAD": {
                "ENABLE_PACS": "SEND_TO_PACS" in request.form,
                "UPLOAD_AE_TITLE": request.form.get("UPLOAD_AE_TITLE", ""),
                "UPLOAD_IP": request.form.get("UPLOAD_IP", ""),
                "UPLOAD_PORT": int(request.form.get("UPLOAD_PORT", 0)),

                "ENABLE_API": "SEND_TO_API" in request.form,
                "API_URL": request.form.get("HIS_API_URL", ""),
            },

            "DEMO": {
                "ORTHANC_WORKLIST_FOLDER": request.form["ORTHANC_WORKLIST_FOLDER"]
            },

            "PATHS": {
                "RESULT_FOLDER": request.form["RESULT_FOLDER"],
                "LOG": load_config()["PATHS"]["LOG"],  # preserve existing
            }
        }

        save_config(updated_config)
        return redirect(url_for("edit_config"))

    # ===== GET =====
    raw = load_config()

    # Flatten for template
    template_config = {
        # Query mode
        "QUERY_MODE": "backend"
        if raw["ORDER"]["ENABLE_BACKEND"]
        else "pacs",

        # Order
        "ORDER_AE_TITLE": raw["ORDER"]["ORDER_AE_TITLE"],
        "ORDER_IP": raw["ORDER"]["ORDER_IP"],
        "ORDER_PORT": raw["ORDER"]["ORDER_PORT"],
        "LOCAL_AE_TITLE": raw["ORDER"]["LOCAL_AE_TITLE"],
        "ORDER_API_ADDRESS": raw["ORDER"].get("ORDER_API_ADDRESS", ""),

        # Upload
        "SEND_TO_PACS": raw["UPLOAD"]["ENABLE_PACS"],
        "UPLOAD_AE_TITLE": raw["UPLOAD"]["UPLOAD_AE_TITLE"],
        "UPLOAD_IP": raw["UPLOAD"]["UPLOAD_IP"],
        "UPLOAD_PORT": raw["UPLOAD"]["UPLOAD_PORT"],

        "SEND_TO_API": raw["UPLOAD"]["ENABLE_API"],
        "HIS_API_URL": raw["UPLOAD"]["API_URL"],

        # Demo / paths
        "ORTHANC_WORKLIST_FOLDER": raw["DEMO"]["ORTHANC_WORKLIST_FOLDER"],
        "RESULT_FOLDER": raw["PATHS"]["RESULT_FOLDER"],
    }

    return render_template("config.html", config=template_config)

@app.route("/worklists", methods=["GET"])
def view_worklists():
    worklist = get_worklists_from_folder()
    return render_template("worklists.html", worklists = worklist)

@app.route("/insert-worklist", methods=["POST"])
def insert_worklist():
    print("=== Received a request ===")
    try:
        # Get JSON data sent from Laravel
        data = request.get_json()

        # Extract and print fields (for debug)
        patient_id = str(data.get('id'))
        name = data.get('name')
        gender = data.get('gender')
        age = str(data.get('age'))

        print(f"Received worklist: ID={patient_id}, Name={name}, Gender={gender}, Age={age}")

        os.makedirs(WORKLIST_FOLDER, exist_ok=True)

        filename = f"worklist_{patient_id}.wl"
        filepath = os.path.join(WORKLIST_FOLDER, filename)

        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = ModalityWorklistInformationFind
        file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
        file_meta.ImplementationClassUID = pydicom.uid.generate_uid()

        ds = FileDataset(filepath, {}, file_meta=file_meta, preamble=b"\0" * 128)
        ds.is_little_endian = True
        ds.is_implicit_VR = True

        ds.PatientName = name.replace(" ", "^")
        ds.PatientSex = gender[0].upper()
        ds.PatientAge = age
        ds.PatientID = patient_id
        ds.AccessionNumber = "ACC" + patient_id.zfill(5)
        ds.StudyInstanceUID = pydicom.uid.generate_uid()

        sps = Dataset()
        sps.ScheduledStationAETitle = "ORTHANC"
        sps.ScheduledProcedureStepStartDate = datetime.date.today().strftime("%Y%m%d")
        sps.ScheduledProcedureStepStartTime = "120000"
        sps.Modality = "ECG"  # Change to MR, CT, etc., if needed
        sps.ScheduledPerformingPhysicianName = "Dr. House"
        sps.ScheduledProcedureStepDescription = "ECG Exam"
        sps.ScheduledStationName = "ECG1"
        sps.ScheduledProcedureStepID = f"SPSID{patient_id}"

        ds.ScheduledProcedureStepSequence = [sps]

        ds.RequestedProcedureID = f"RPID{patient_id}"
        ds.RequestedProcedureDescription = "ECG Worklist"

        ds.save_as(filepath, write_like_original=False)

        print(f"[INFO] Worklist file saved to {filepath}")
        return jsonify({"success": True, "message": "Worklist saved."}), 200
    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/info")
def info():
    config = load_config()

    status = {
        "local_ae": config["LOCAL_AE_TITLE"],
        "upload_port": config["UPLOAD_PORT"],
        "order_ae": config["ORDER_AE_TITLE"]
    }

    logs = list(LOG_BUFFER)

    return render_template(
        "info.html",
        status=status,
        logs=logs
    )


print("STEP 3: before app.run")
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
