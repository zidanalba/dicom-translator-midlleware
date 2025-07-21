import os
import pydicom

def unpack_wl_file(filepath):
    try:
        ds = pydicom.dcmread(filepath)
        print(f"--- Contents of {os.path.basename(filepath)} ---")
        for elem in ds:
            tag = elem.tag
            keyword = elem.keyword if elem.keyword else str(tag)
            value = elem.value
            print(f"{keyword} ({tag}): {value}")
    except Exception as e:
        print(f"Failed to read {filepath}: {e}")
