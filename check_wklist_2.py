import pydicom

ds = pydicom.dcmread("C:/Orthanc/Worklists/wklist_1.dcm")
print("PatientID:", ds.get("PatientID"))
print("PatientName:", ds.get("PatientName"))

if "ScheduledProcedureStepSequence" in ds:
    sps = ds.ScheduledProcedureStepSequence[0]
    print("Scheduled Procedure Step ID:", sps.get("ScheduledProcedureStepID"))
    print("Modality:", sps.get("Modality"))
    print("ScheduledStationAETitle:", sps.get("ScheduledStationAETitle"))
else:
    print("No ScheduledProcedureStepSequence found.")
