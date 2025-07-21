from pydicom.dataset import Dataset, FileDataset
from pynetdicom.sop_class import ModalityWorklistInformationFind
import pydicom.uid
import datetime

filename = "wklist_4.wl"

# File meta info
file_meta = Dataset()
file_meta.MediaStorageSOPClassUID = ModalityWorklistInformationFind
file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
file_meta.ImplementationClassUID = pydicom.uid.generate_uid()

# Dataset
ds = FileDataset(filename, {}, file_meta=file_meta, preamble=b"\0" * 128)
ds.is_little_endian = True
ds.is_implicit_VR = True

# Add patient info
ds.PatientName = "Iyas Lawrence"
ds.PatientSex = "M"
ds.PatientAge = "029Y"
# ds.Pacemaker = "2"
ds.PatientID = "4"
ds.AccessionNumber = "123458"
ds.StudyInstanceUID = pydicom.uid.generate_uid()

# Required: Scheduled Procedure Step Sequence
sps = Dataset()
sps.ScheduledStationAETitle = "ORTHANC"
sps.ScheduledProcedureStepStartDate = datetime.date.today().strftime("%Y%m%d")
sps.ScheduledProcedureStepStartTime = "123000"
sps.Modality = "MR"
sps.ScheduledPerformingPhysicianName = "Dr. House"
sps.ScheduledProcedureStepDescription = "Brain MRI"
sps.ScheduledStationName = "MRI1"
sps.ScheduledProcedureStepID = "SPSID123"

ds.ScheduledProcedureStepSequence = [sps]

# Required for MWL query
ds.RequestedProcedureID = "RPID123"
ds.RequestedProcedureDescription = "MRI Head"

# Save the file
ds.save_as(filename, write_like_original=False)
print(f"Worklist saved to {filename}")
