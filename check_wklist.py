import pydicom

ds = pydicom.dcmread("C:/Orthanc/Worklists/wklist_1.wl")

print("PatientID:", ds.get("PatientID", "MISSING"))
print("ScheduledProcedureStepSequence:", "OK" if "ScheduledProcedureStepSequence" in ds else "MISSING")

if "ScheduledProcedureStepSequence" in ds:
    sps = ds.ScheduledProcedureStepSequence[0]
    print("ScheduledStationAETitle:", sps.get("ScheduledStationAETitle", "MISSING"))
    print("Modality:", sps.get("Modality", "MISSING"))
