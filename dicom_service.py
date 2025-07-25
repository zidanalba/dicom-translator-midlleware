import json
import os
import threading
from pynetdicom import AE
from pynetdicom.sop_class import ModalityWorklistInformationFind

# Load config.json
with open('config.json', 'r') as f:
    config = json.load(f)

ae = None
server_thread = None

def start_service():
    global ae
    with open('config.json', 'r') as f:
        config = json.load(f)

    local_ae_title = config.get("LOCAL_AE_TITLE", "ECG_BRIDGE")
    orthanc_port = int(config.get("ORTHANC_PORT", 4242))

    ae = AE(ae_title=local_ae_title)
    ae.add_supported_context(ModalityWorklistInformationFind)
    ae.start_server(('', orthanc_port), block=True)


def run_service():
    global server_thread
    if server_thread and server_thread.is_alive():
        return  # Already running
    server_thread = threading.Thread(target=start_service)
    server_thread.daemon = True
    server_thread.start()

def stop_service():
    global ae
    if ae:
        ae.shutdown()
