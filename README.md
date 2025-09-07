# Servants of Knowledge 

Servants of Knowledge is an effort to digitize books in libraries for archival reasons, search purposes and all use cases permitted by the copyright laws. The aim is to provide full stack solution for scanning books that includes hardware for scanning books and the software to process the images into a PDF. Also to enable a full text search on these books and the management of these books in a library. 

For details on how to build your own scanners or buy a scanner from us, please message us. The current software repository only provides the necessary software to process the scans and then organize the books for a full text search or running a library management system. 

This repository contains work for the Servants modifications to InvenioILS (Integrated Library System) and the REPUB document processing system for digitizing and managing scanned books and documents.

### REPUB Features

REPUB is a comprehensive document processing pipeline that converts scanned book pages into high-quality, searchable digital documents. Key features include:

- **Image Processing**: Auto-cropping, deskewing and dewarping for scanned images
- **OCR Integration**: Multi-language text recognition using Tesseract
- **PDF Generation**: Creates searchable PDFs with embedded text layers
- **Command-line Tool**: For directly processing the scanned images into PDF, HOCR file and thumbnails 
- **Web Interface**: Django-based UI for job management and processing workflows along with a client that can submit jobs

## Quick Start

### Prerequisites

- Python 3.8+
- OpenCV
- Tesseract OCR
- Django 5.2
- PostgreSQL (for production)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd servantsofknowledge
```

2. Install Python dependencies:
```bash
pip install -r repub/requirements.txt
```

3. Install system dependencies:
```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra tesseract-ocr-deu
sudo apt-get install poppler-utils

# macOS
brew install tesseract poppler
```

## REPUB Usage

### Command Line Processing

The `process_raw.py` tool provides direct command-line access to the document processing pipeline:

#### Basic Usage

```bash
cd repub/

# Process images from a directory
python process_raw.py -i input_directory -o output_directory --crop --deskew --ocr

# Process a PDF file
python process_raw.py -I input.pdf -O output.pdf --crop --deskew --ocr

# Create Internet Archive compatible output
python process_raw.py -i input_directory -A ia_output_directory
```

#### Common Options

| Option | Description |
|--------|-------------|
| `-i, --indir` | Input directory containing scanned images |
| `-I, --inpdf` | Input PDF file to process |
| `-o, --outdir` | Output directory for processed images |
| `-O, --outpdf` | Output PDF file path |
| `-A, --iadir` | Internet Archive format output directory |
| `-c, --crop` | Auto-crop page boundaries |
| `-D, --deskew` | Detect and correct page skew |
| `-t, --ocr` | Perform OCR and create searchable PDF |
| `-L, --language` | OCR language (default: eng) |
| `-r, --reduce` | Scale images by factor (e.g., 0.5 for 50%) |
| `-w, --dewarp` | Apply dewarping to curved pages |
| `-m, --maxcontours` | Max contours for cropping analysis (default: 5) |


### Web Interface (RepubUI)

The Django-based web interface provides a user-friendly way to manage document processing jobs:

#### Setup

1. Navigate to the web application directory:
```bash
cd repub/repubui/
```

2. Configure environment variables:
```bash
# Create .env file
cp .env.example .env
# Edit .env with your settings
```

3. Set up the database:
```bash
python manage.py makemigrations
python manage.py migrate
```

4. Create a superuser account:
```bash
python manage.py createsuperuser
```

5. Start the development server:
```bash
python manage.py runserver
```

#### Web Interface Features

- **Job Management**: Upload, monitor, and manage processing jobs
- **Page-by-Page Review**: Individual page editing and adjustment
- **Batch Processing**: Handle multiple documents simultaneously
- **Admin Interface**: User management and system configuration
- **API Access**: RESTful API for programmatic access

#### Access Points

- **Main Interface**: http://localhost:8000/
- **Admin Panel**: http://localhost:8000/admin/
- **API Documentation**: http://localhost:8000/api/

## InvenioILS Integration

The `invenioils/` directory contains a modified InvenioILS installation that integrates with the REPUB processing system:

## Project Structure

```
servantsofknowledge/
├── invenioils/              # Modified InvenioILS system
│   ├── iarchive/           # Internet Archive integration
│   ├── ILS/                # Core InvenioILS application
│   └── reactjs/            # React components
├── repub/                   # Document processing system
│   ├── imgfuncs/           # Image processing functions
│   │   ├── cropping.py     # Auto-cropping algorithms
│   │   ├── deskew.py       # Skew detection and correction
│   │   ├── dewarp.py       # Page dewarping
│   │   └── utils.py        # Image utilities
│   ├── utils/              # Processing utilities
│   │   ├── pdfs.py         # PDF operations
│   │   ├── hocrproc.py     # HOCR processing
│   │   └── scandir.py      # Directory scanning
│   ├── repubui/            # Django web application
│   │   ├── repub_interface/# Main Django app
│   │   ├── templates/      # HTML templates
│   │   └── static/         # Static assets
│   ├── process_raw.py      # Command-line interface
│   └── requirements.txt    # Python dependencies
└── README.md               # This file
```

## Configuration

### Environment Variables (.env)

For the RepubUI Django application:

```bash
SECRET_KEY=your-secret-key
DEBUG=True
DEPLOYMENT=local  # or 'prod' for PostgreSQL
ALLOWED_HOSTS=localhost,127.0.0.1
```
