print("STEP 1: app.py loaded")
# from pynetdicom import debug_logger

# debug_logger()
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
import io
import time
import datetime
import requests
import sys
import threading
from collections import deque
from flask import jsonify
from xml.etree.ElementTree import Element, SubElement, tostring
from pydicom.valuerep import PersonName
from pydicom.errors import InvalidDicomError
from enum import Enum
from pynetdicom.sop_class import (
    SecondaryCaptureImageStorage,
    CTImageStorage,
)
from pydicom.uid import (
    ImplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ExplicitVRBigEndian,
    JPEGBaseline8Bit,
    JPEGExtended12Bit,
)

class PayloadType(str, Enum):
    # DICOM family
    DICOM = "DICOM"
    PDF_DCM = "PDF_DCM"
    IMAGE_DCM = "IMAGE_DCM"   # JPEG-DCM, BMP-DCM, etc

    # Non-DICOM
    RAW_PDF = "RAW_PDF"
    RAW_IMAGE = "RAW_IMAGE"   # JPG, JPEG, BMP, TIFF
    FDA_XML = "FDA_XML"
    SCP = "SCP"
    DAT = "DAT"

    UNKNOWN = "UNKNOWN"

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

def send_dicom_to_orthanc(dicom_path, dest_ae=UPLOAD_AE_TITLE,
                          dest_host=UPLOAD_IP, dest_port=UPLOAD_PORT):

    config = load_config()
    upload_cfg = config["UPLOAD"]

    ds = dcmread(dicom_path)

    if ds.file_meta.TransferSyntaxUID.is_compressed:
        ds.decompress()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.is_little_endian = True
        ds.is_implicit_VR = False

    ae = AE(ae_title=upload_cfg["UPLOAD_AE_TITLE"])

    ae.add_requested_context(
        ds.SOPClassUID,
        [
            ExplicitVRLittleEndian,
            ImplicitVRLittleEndian,
            JPEGBaseline8Bit,
            JPEGExtended12Bit,
        ]
    )

    assoc = ae.associate(dest_host, dest_port, ae_title=dest_ae)

    if not assoc.is_established:
        raise RuntimeError("Could not establish association with Orthanc")

    status = assoc.send_c_store(ds)

    if not status:
        raise RuntimeError("C-STORE failed")

    assoc.release()


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

def map_patient_sex(value) -> str:
    if not value:
        return ''

    v = str(value).strip().lower()

    if v in ('m', 'male', 'laki-laki', 'laki', 'pria'):
        return 'M'
    if v in ('f', 'female', 'perempuan', 'wanita'):
        return 'F'

    return ''

def backend_to_xml_response(data: dict) -> str:
    root = Element('root')

    # Kode sukses dan pesan (WAJIB & URUTAN PENTING)
    code = SubElement(root, 'Code')
    code.text = '1'

    message = SubElement(root, 'Message')
    message.text = ''

    # records -> rows (WAJIB)
    records = SubElement(root, 'records')
    rows = SubElement(records, 'rows')

    # Helper untuk aman dari None
    def val(key, default=''):
        v = data.get(key)
        return '' if v is None else str(v)

    SubElement(rows, 'SerialNo').text = val('SerialNo')
    SubElement(rows, 'PatientID').text = val('PatientID')
    SubElement(rows, 'PatientName').text = val('PatientName')
    SubElement(rows, 'PatientSex').text = map_patient_sex(data.get('PatientSex'))
    SubElement(rows, 'PatientAge').text = val('PatientAge')
    SubElement(rows, 'PatientAgeUnit').text = 'Y'   # HARDCODE seperti DICOM
    SubElement(rows, 'PatientBirthDate').text = val('PatientBirthDate')
    SubElement(rows, 'RequestDepartment').text = val('RequestDepartment')
    SubElement(rows, 'RequestID').text = val('RequestID')
    SubElement(rows, 'SickBedNo').text = val('SickBedNo')
    SubElement(rows, 'Pacemaker').text = '2'        # HARDCODE seperti DICOM
    SubElement(rows, 'ExamDepartment').text = val('ExamDepartment')
    SubElement(rows, 'Priority').text = val('Priority')
    SubElement(rows, 'fileGuid').text = val('fileGuid')
    SubElement(rows, 'RequestDate').text = val('RequestDate')

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + tostring(root, encoding='unicode')
    )

    logging.info("Generated BACKEND XML:\n%s", xml)

    return xml

def query_backend_service(patient_id: str, api_address: str):
    if not api_address:
        raise ValueError("ORDER_API_ADDRESS is empty")

    try:
        logging.info(f"Query backend service for PatientID={patient_id}")

        response = requests.get(
            api_address,
            params={"patient_id": patient_id},
            timeout=10
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
            logging.info("Generated PACS XML:\n%s", xml_response)

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

def classify_payload(file_path):
    ext = os.path.splitext(file_path.lower())[1]

    if ext == ".dcm":
        try:
            ds = pydicom.dcmread(file_path, force=True, stop_before_pixels=True)

            sop_class = str(ds.get("SOPClassUID", ""))

            if sop_class == "1.2.840.10008.5.1.4.1.1.104.1":
                return PayloadType.PDF_DCM, ds

            if sop_class.startswith("1.2.840.10008.5.1.4.1.1"):
                return PayloadType.IMAGE_DCM, ds

            return PayloadType.DICOM, ds

        except InvalidDicomError:
            return PayloadType.UNKNOWN, None

    if ext == ".xml":
        return PayloadType.FDA_XML, None

    if ext == ".scp":
        return PayloadType.SCP, None

    if ext in (".jpg", ".jpeg", ".bmp", ".tiff", ".tif"):
        return PayloadType.RAW_IMAGE, None

    if ext == ".dat":
        return PayloadType.DAT, None

    return PayloadType.UNKNOWN, None

def log_incoming_request(request, log_path):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    with open(log_path, "a", encoding="utf-8") as log:
        log.write("\n\n================ NEW REQUEST ================\n")

        log.write(f"Method: {request.method}\n")
        log.write(f"Remote Addr: {request.remote_addr}\n")
        log.write(f"Content-Type: {request.content_type}\n")
        log.write(f"Content-Length: {request.content_length}\n\n")

        log.write("=== HEADERS ===\n")
        for k, v in request.headers.items():
            log.write(f"{k}: {v}\n")
        log.write("\n")

        if request.content_type and request.content_type.startswith("multipart/form-data"):
            log.write("=== MULTIPART FORM ===\n")
            log.write(f"Form keys: {list(request.form.keys())}\n")
            log.write(f"File keys: {list(request.files.keys())}\n\n")

            for key, file in request.files.items():
                file_bytes = file.read()
                file.seek(0)

                log.write(f"[FILE FIELD] {key}\n")
                log.write(f"  Filename     : {file.filename}\n")
                log.write(f"  Content-Type : {file.content_type}\n")
                log.write(f"  Size         : {len(file_bytes)} bytes\n")
                log.write(f"  First 64 bytes (hex): {file_bytes[:64].hex()}\n\n")

        else:
            raw = request.get_data()
            log.write("=== RAW BODY ===\n")
            log.write(f"Raw size: {len(raw)} bytes\n")
            log.write(f"First 64 bytes (hex): {raw[:64].hex()}\n\n")

        log.write("============== END REQUEST ==================\n")

def save_incoming_file(request, upload_dir):
    os.makedirs(upload_dir, exist_ok=True)

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        if not request.files:
            raise ValueError("No file part in multipart upload")

        uploaded_file = next(iter(request.files.values()))
        filename = uploaded_file.filename or f"upload_{int(time.time())}"
        data = uploaded_file.read()
    else:
        filename = request.headers.get("Filename") or f"upload_{int(time.time())}"
        data = request.get_data()

    if not data:
        raise ValueError("Empty payload")

    safe_name = os.path.basename(filename)
    save_path = os.path.join(upload_dir, safe_name)

    with open(save_path, "wb") as f:
        f.write(data)

    logging.info(f"Saved incoming file to {save_path}")
    return save_path

def classify_payload(file_path):
    ext = os.path.splitext(file_path.lower())[1]

    # DICOM
    if ext == ".dcm":
        try:
            ds = pydicom.dcmread(file_path, force=True, stop_before_pixels=True)
            sop = str(ds.get("SOPClassUID", ""))

            if sop == "1.2.840.10008.5.1.4.1.1.104.1":
                return PayloadType.PDF_DCM, ds

            if sop.startswith("1.2.840.10008.5.1.4.1.1"):
                return PayloadType.IMAGE_DCM, ds

            return PayloadType.DICOM, ds
        except Exception:
            return PayloadType.UNKNOWN, None

    if ext == ".xml":
        return PayloadType.FDA_XML, None

    if ext == ".scp":
        return PayloadType.SCP, None

    if ext in (".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
        return PayloadType.RAW_IMAGE, None

    if ext == ".pdf":
        return PayloadType.RAW_PDF, None

    if ext == ".dat":
        return PayloadType.DAT, None

    return PayloadType.UNKNOWN, None

def route_to_pacs(payload_type, file_path):
    if payload_type in (
        PayloadType.DICOM,
        PayloadType.PDF_DCM,
        PayloadType.IMAGE_DCM,
    ):
        logging.info("Routing to PACS")
        send_dicom_to_orthanc(file_path)
    else:
        logging.info(f"Skipping PACS for {payload_type}")


def send_pdf_to_his(pdf_bytes, ds):
    config = load_config()
    url = config["UPLOAD"]["API_URL"]

    patient_id = getattr(ds, "PatientID", "")
    study_date = getattr(ds, "StudyDate", "")
    exam_name = getattr(ds, "StudyDescription", "ECG")

    files = {
        "pdf": (
            "ecg_result.pdf",
            io.BytesIO(pdf_bytes),
            "application/pdf"
        )
    }

    data = {
        "patient_id": patient_id,
        "exam_name": exam_name,
        "study_date": study_date,
    }

    logging.info(
        "Sending PDF to HIS | patient_id=%s exam=%s date=%s",
        patient_id, exam_name, study_date
    )

    resp = requests.post(url, files=files, data=data, timeout=15)

    if resp.status_code != 200:
        logging.error("HIS error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    logging.info("PDF successfully sent to HIS: %s", resp.json())

def route_to_his(payload_type, ds, file_path):
    logging.info(f"Routing to HIS ({payload_type})")

    if payload_type == PayloadType.PDF_DCM and ds and hasattr(ds, "EncapsulatedDocument"):
        send_pdf_to_his(ds.EncapsulatedDocument, ds)
        print("send pdf to his")

    elif payload_type == PayloadType.RAW_PDF:
        with open(file_path, "rb") as f:
            print("send pdf to his")
            # send_pdf_to_his(f.read())

    elif payload_type == PayloadType.RAW_IMAGE:
        with open(file_path, "rb") as f:
            print("send image to his")
            # send_image_to_his(f.read())

    elif payload_type == PayloadType.FDA_XML:
        with open(file_path, "rb") as f:
            print("send xml to his")
            # send_xml_to_his(f.read())

    else:
        logging.warning(f"HIS routing not supported for {payload_type}")


@app.route("/receive", methods=["POST"])
def receive():
    log_path = os.path.join(RESULT_FOLDER, "request_debug.log")
    os.makedirs(RESULT_FOLDER, exist_ok=True)
    config = load_config()
    upload_cfg = config["UPLOAD"]

    try:
        log_incoming_request(request, log_path)
        
        save_path = save_incoming_file(request, RESULT_FOLDER)

        payload_type, ds = classify_payload(save_path)
        logging.info(f"[CLASSIFY] Payload type: {payload_type}")

        if upload_cfg["ENABLE_PACS"]:
            route_to_pacs(payload_type, save_path)

        if upload_cfg["ENABLE_API"]:
            route_to_his(payload_type, ds, save_path)

        return xml_response(1, "Upload successful")

    except Exception as e:
        logging.exception("Receive error")
        return xml_response(-1, str(e))

@app.route("/receive-two", methods=["POST"])
def receive_file():
    try:
        filename = None
        file_content = None

        log_path = os.path.join(UPLOAD_FOLDER, "request_debug.log")
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("\n\n=== New Request ===\n")

            log.write("=== Incoming Headers ===\n")
            for k, v in request.headers.items():
                log.write(f"{k}: {v}\n")
            log.write("========================\n")

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

        old = load_config()

        updated_config = {
            "MODE": "demo",

            "ORDER": {
                "ENABLE_PACS": query_mode == "pacs",
                "ENABLE_BACKEND": query_mode == "backend",

                "LOCAL_AE_TITLE": request.form.get("ORDER_LOCAL_AE_TITLE", ""),
                "ORDER_AE_TITLE": request.form.get("ORDER_AE_TITLE", ""),
                "ORDER_IP": request.form.get("ORDER_IP", ""),
                "ORDER_PORT": int(request.form.get("ORDER_PORT", 0)),
                "ORDER_API_ADDRESS": request.form.get("ORDER_API_ADDRESS", ""),
            },

            "UPLOAD": {
                "ENABLE_PACS": "SEND_TO_PACS" in request.form,
                "UPLOAD_AE_TITLE": request.form.get("UPLOAD_AE_TITLE", ""),
                "LOCAL_AE_TITLE": request.form.get("UPLOAD_LOCAL_AE_TITLE", ""),
                "UPLOAD_IP": request.form.get("UPLOAD_IP", ""),
                "UPLOAD_PORT": int(request.form.get("UPLOAD_PORT", 0)),

                "ENABLE_API": "SEND_TO_API" in request.form,
                "API_URL": request.form.get("HIS_API_URL", ""),
            },

            "DEMO": {
                "ORTHANC_WORKLIST_FOLDER": request.form.get(
                    "ORTHANC_WORKLIST_FOLDER", ""
                )
            },

            "PATHS": {
                "RESULT_FOLDER": request.form.get("RESULT_FOLDER", ""),
                "LOG": old["PATHS"]["LOG"],  # preserve
            },
        }

        save_config(updated_config)
        return redirect(url_for("edit_config"))

    # ===== GET =====
    raw = load_config()

    template_config = {
        # Query mode
        "QUERY_MODE": "backend"
        if raw["ORDER"]["ENABLE_BACKEND"]
        else "pacs",

        # ORDER
        "ORDER_AE_TITLE": raw["ORDER"]["ORDER_AE_TITLE"],
        "ORDER_LOCAL_AE_TITLE": raw["ORDER"]["LOCAL_AE_TITLE"],
        "ORDER_IP": raw["ORDER"]["ORDER_IP"],
        "ORDER_PORT": raw["ORDER"]["ORDER_PORT"],
        "ORDER_API_ADDRESS": raw["ORDER"].get("ORDER_API_ADDRESS", ""),

        # UPLOAD
        "SEND_TO_PACS": raw["UPLOAD"]["ENABLE_PACS"],
        "UPLOAD_AE_TITLE": raw["UPLOAD"]["UPLOAD_AE_TITLE"],
        "UPLOAD_LOCAL_AE_TITLE": raw["UPLOAD"]["LOCAL_AE_TITLE"],
        "UPLOAD_IP": raw["UPLOAD"]["UPLOAD_IP"],
        "UPLOAD_PORT": raw["UPLOAD"]["UPLOAD_PORT"],

        "SEND_TO_API": raw["UPLOAD"]["ENABLE_API"],
        "HIS_API_URL": raw["UPLOAD"]["API_URL"],

        # DEMO / PATHS
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
