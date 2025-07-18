from pydicom.dataset import Dataset, FileDataset
from pynetdicom.sop_class import ModalityWorklistInformationFind
import pydicom
import datetime
import os

filename = "C:/Orthanc/Worklists/wklist_1.dcm"

# Buat dataset kosong
ds = Dataset()

# Informasi dasar pasien
ds.PatientName = "TEST^Patient"
ds.PatientID = "1"
ds.PatientBirthDate = "19800101"
ds.PatientSex = "M"

# Informasi worklist yang penting
ds.AccessionNumber = "123456"
ds.Modality = "MR"
ds.StudyInstanceUID = pydicom.uid.generate_uid()
ds.RequestedProcedureID = "RPID001"
ds.RequestedProcedureDescription = "MRI Brain"
ds.ScheduledProcedureStepID = "SPSID001"
ds.ScheduledStationAETitle = "ORTHANC"
ds.ScheduledStationName = "ORTHANC_NODE"
ds.ScheduledProcedureStepDescription = "MRI Head without contrast"
ds.ScheduledPerformingPhysicianName = "DR^House"

# ScheduledProcedureStepSequence wajib
sps = Dataset()
sps.Modality = "MR"
sps.ScheduledStationAETitle = "ORTHANC"
sps.ScheduledProcedureStepStartDate = datetime.date.today().strftime("%Y%m%d")
sps.ScheduledProcedureStepStartTime = datetime.datetime.now().strftime("%H%M%S")
sps.ScheduledPerformingPhysicianName = "DR^House"
sps.ScheduledProcedureStepDescription = "MRI Head"
sps.ScheduledStationName = "ORTHANC_NODE"
sps.ScheduledProcedureStepID = "SPSID001"

# Masukkan ke sequence
ds.ScheduledProcedureStepSequence = [sps]

# Buat FileDataset untuk menyimpan file DICOM yang valid
file_meta = Dataset()
file_meta.MediaStorageSOPClassUID = ModalityWorklistInformationFind
file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
file_meta.ImplementationClassUID = pydicom.uid.generate_uid()

ds = FileDataset(filename, ds, file_meta=file_meta, preamble=b"\0" * 128)
ds.is_little_endian = True
ds.is_implicit_VR = True

ds.save_as(filename)
print("Worklist saved:", filename)
