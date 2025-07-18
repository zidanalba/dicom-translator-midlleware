from flask import Flask, request, Response, render_template, jsonify, redirect, url_for
from lxml import etree
from pynetdicom import AE
from pynetdicom.sop_class import ModalityWorklistInformationFind
from pydicom.dataset import Dataset
from xml.etree.ElementTree import Element, SubElement, tostring
import logging
import json
import os

app = Flask(__name__)

CONFIG_PATH = "config.json"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=4)

config = load_config()

ORTHANC_AE_TITLE = config["ORTHANC_AE_TITLE"]
ORTHANC_IP = config["ORTHANC_IP"]
ORTHANC_PORT = config["ORTHANC_PORT"]
LOCAL_AE_TITLE = config["LOCAL_AE_TITLE"]

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

    assoc = ae.associate(ORTHANC_IP, ORTHANC_PORT, ae_title=ORTHANC_AE_TITLE)
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

@app.route("/query", methods=["POST"])
def query_worklist():
    try:
        xml_data = request.data
        patient_id = parse_patient_id_from_xml(xml_data)
        logging.info(f"Received request for PatientID: {patient_id}")

        dicom_response = dicom_cfind(patient_id)

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

# @app.route("/receive", methods=["POST"])
# def receive_and_send_result():
#     app.logger.info(f"Incoming data: {request.data}")

@app.route("/config", methods=["GET", "POST"])
def edit_config():
    if request.method == "POST":
        updated_config = {
            "ORTHANC_AE_TITLE": request.form["ORTHANC_AE_TITLE"],
            "ORTHANC_IP": request.form["ORTHANC_IP"],
            "ORTHANC_PORT": int(request.form["ORTHANC_PORT"]),
            "LOCAL_AE_TITLE": request.form["LOCAL_AE_TITLE"],
        }
        save_config(updated_config)
        return redirect(url_for('edit_config'))
    
    current_config = load_config()
    return render_template("config.html", config=current_config)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
