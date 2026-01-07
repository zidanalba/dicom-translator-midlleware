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

app = Flask(__name__)

CONFIG_PATH = "config.json"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=4)

config = load_config()

ORDER_AE_TITLE = config["ORDER_AE_TITLE"]
ORDER_IP = config["ORDER_IP"]
ORDER_PORT = config["ORDER_PORT"]
UPLOAD_AE_TITLE = config["UPLOAD_AE_TITLE"]
UPLOAD_IP = config["UPLOAD_IP"]
UPLOAD_PORT = config["UPLOAD_PORT"]
LOCAL_AE_TITLE = config["LOCAL_AE_TITLE"]
WORKLIST_FOLDER = config["ORTHANC_WORKLIST_FOLDER"]
HIS_API_URL = config["HIS_API_URL"]
UPLOAD_FOLDER = config["RESULT_FOLDER"]
SEND_TO_API = config["SEND_TO_API"]
RESULT_FOLDER = config["RESULT_FOLDER"]
IS_QUERY_PACS = config["IS_QUERY_PACS"]
IS_QUERY_BACKEND_SERVICE = config["IS_QUERY_BACKEND_SERVICE"]

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
    SubElement(rows, 'PatientName').text = str(patient_name) if patient_name else ''

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

        # if IS_QUERY_PACS:
        dicom_response = dicom_cfind(patient_id)

        # if IS_QUERY_BACKEND_SERVICE:


        if dicom_response:
            xml_response = dicom_to_xml_response(dicom_response)
        else:
            xml_response = etree.tostring(
                etree.Element("Error", message="Patient not found"),
                pretty_print=True, xml_declaration=True, encoding='UTF-8'
            )

        return Response(xml_response, mimetype='application/xml')

    except Exception as e:
        logging.exception("Error in query_worklist")
        return Response(
            etree.tostring(etree.Element("Error", message=str(e)), pretty_print=True, xml_declaration=True, encoding='UTF-8'),
            mimetype='application/xml',
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
                log.write(f"📝 Extracted PDF saved to: {pdf_path}\n")
        else:
            with open(log_path, "a", encoding="utf-8") as log:
                log.write("ℹ️ No EncapsulatedDocument found in DICOM file\n")

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
                # 📦 Old ECG (raw octet-stream + Filename header)
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
        updated_config = {
            "ORDER_AE_TITLE": request.form["ORDER_AE_TITLE"],
            "ORDER_IP": request.form["ORDER_IP"],
            "ORDER_PORT": int(request.form["ORDER_PORT"]),
            "UPLOAD_AE_TITLE": request.form["UPLOAD_AE_TITLE"],
            "UPLOAD_IP": request.form["UPLOAD_IP"],
            "UPLOAD_PORT": int(request.form["UPLOAD_PORT"]),
            "LOCAL_AE_TITLE": request.form["LOCAL_AE_TITLE"],
            "ORTHANC_WORKLIST_FOLDER": request.form["ORTHANC_WORKLIST_FOLDER"],
            "SEND_TO_API": request.form.get("SEND_TO_API") == "true",
            "HIS_API_URL": request.form["HIS_API_URL"],
            "RESULT_FOLDER": request.form["RESULT_FOLDER"],
        }
        save_config(updated_config)
        return redirect(url_for('edit_config'))
    
    current_config = load_config()
    return render_template("config.html", config=current_config)

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
    return "Middleware is running!", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
