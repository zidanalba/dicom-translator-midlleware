import pydicom

ds = pydicom.dcmread("C:/Orthanc/Worklists/wklist_1.dcm")
print(ds.PatientID)
