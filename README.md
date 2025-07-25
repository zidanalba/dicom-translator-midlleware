# DICOM Translator Middleware

A lightweight Flask-based middleware to receive ECG result files from medical devices and forward them to a DICOM PACS server (e.g., Orthanc) using the DICOM protocol. This repo is build to fulfill my task to integrate Dawei's ECG (that didn't have DICOM protocol) to a PACS Server, for this case i use ORTHANC as the PACS Server.

---

## Features

- Accepts HTTP POST of ECG results (PDF/XML)
- Converts and forwards results as DICOM Secondary Capture (SC)
- Sends to configurable PACS server (Orthanc, etc.)
- Configurable via `config.json` and `http://localhost:8080/config`

---

## Installation

1. **Clone the repo**

```bash
git clone https://github.com/zidanakba/dicom-translator-middleware.git
cd dicom-translator-middleware
```

2. **Create and Activate Virtual Environment**

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate
```

2. **Install Requirements**

```bash
pip install -r requirements.txt
```

## Usage

Use the provided batch script to start the Flask server:

```bash
start.bat
```

Or manually:
```bash
venv\Scripts\activate
python app.py
```

