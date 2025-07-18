import requests
import json

# Orthanc server details
ORTHANC_URL = "http://localhost:8042"
USERNAME = "orthanc"
PASSWORD = "orthanc"

# Worklist data
worklist_data = {
    "PatientID": "123456",
    "PatientName": "John Doe",
    "PatientBirthDate": "19800101",
    "Modality": "CT",
    "ScheduledProcedureStepSequence": [
        {
            "ScheduledProcedureStepStartDate": "20250717",
            "ScheduledProcedureStepStartTime": "090000",
            "ScheduledPerformingPhysicianName": "Dr. Smith",
            "ScheduledProcedureStepDescription": "CT Abdomen",
            "ScheduledStationAETitle": "CT_SCANNER"
        }
    ]
}

# Convert the worklist data to JSON
worklist_json = json.dumps(worklist_data)

# Send the POST request to insert the worklist
response = requests.post(
    f"{ORTHANC_URL}/worklists",
    data=worklist_json,
    auth=(USERNAME, PASSWORD),
    headers={"Content-Type": "application/json"}
)

# Check the response
if response.status_code == 200:
    print("Worklist inserted successfully!")
    print("Response:", response.json())
else:
    print("Failed to insert worklist.")
    print("Status Code:", response.status_code)
    print("Response:", response.text)
