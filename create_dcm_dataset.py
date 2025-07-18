import pydicom
from pydicom.dataset import Dataset, FileDataset
from datetime import datetime
import uuid

filename = "wklist_1_fixed.dcm"

# Metadata
file_meta = pydicom.dataset.FileMetaDataset()
file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.31"  # Worklist SOP Class UID
file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

# Dataset utama
ds = FileDataset(filename, {}, file_meta=file_meta, preamble=b"\0" * 128)
ds.PatientName = "DOE^JOHN"
ds.PatientID = "1"
ds.AccessionNumber = "12345"
ds.StudyInstanceUID = pydicom.uid.generate_uid()
ds.SeriesInstanceUID = pydicom.uid.generate_uid()
ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
ds.SOPClassUID = file_meta.MediaStorageSOPClassUID

now = datetime.now()
ds.StudyDate = now.strftime("%Y%m%d")
ds.StudyTime = now.strftime("%H%M%S")

# Worklist fields
sps = Dataset()
sps.ScheduledStationAETitle = "ECG_BRIDGE"
sps.Modality = "ECG"
sps.ScheduledProcedureStepStartDate = now.strftime("%Y%m%d")
sps.ScheduledProcedureStepStartTime = now.strftime("%H%M%S")
sps.ScheduledPerformingPhysicianName = "DR.SMITH"
sps.ScheduledProcedureStepDescription = "Routine ECG"
sps.ScheduledProcedureStepID = "ECG001"

ds.ScheduledProcedureStepSequence = [sps]

# Save file
ds.is_little_endian = True
ds.is_implicit_VR = False
ds.save_as(filename)
print(f"Worklist file saved to {filename}")
