import pydicom

# Load an existing DICOM file
ds = pydicom.dcmread(r"C:\Users\zmuha\Documents\it-type-shit\dicom-translator-midlleware\results\4_20250721085054_ekg.dcm")

# Read the Modality field
print("Modality:", ds.get("Modality", "Not found"))