# REPUB Project

REPUB is a powerful document processing tool that transforms scanned images into PDFs with searchable text layers, featuring automatic cropping, deskewing, and OCR capabilities.

## Table of Contents

- [Installation](#installation)
- [Command-Line Usage](#command-line-usage)
  - [Examples](#examples)
  - [Options](#options)
- [RepuBUI Web Interface](#repub-ui-web-interface)
  - [Setup](#setup)
  - [Features](#features)
  - [Directory Structure](#directory-structure)

## Installation

### Prerequisites

- Python 3.6+
- Tesseract OCR Engine

### Setup Process

1. **Install Python Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Link REPUB Package**

   Add the REPUB module to your Python path:

   ```bash
   ln -s <maindirectory>/servantsofknowledge/repub <python_site_package_dir>/repub
   ```

3. **Install Tesseract OCR**

   #### Debian/Ubuntu:
   ```bash
   sudo apt install tesseract-ocr tesseract-ocr-all
   ```

   #### macOS:
   ```bash
   brew install tesseract
   ```

   #### Windows:
   Download the installer from [Tesseract GitHub](https://github.com/UB-Mannheim/tesseract/wiki)

## Command-Line Usage

The core functionality is provided by `process_raw.py`, which converts scanned images or PDFs into searchable PDFs with text layers.

### Examples

**Process Images from Internet Archive:**

```bash
python process_raw.py -i <ia_scan_dir> -O <output_file_path.pdf> -c -L eng+asm
```

**Convert PDF to Searchable PDF:**

```bash
python process_raw.py -I <input_file_path.pdf> -O <output_file_path.pdf> -c -L eng+kan
```

### Options

| Option                         | Description                               |
|--------------------------------|-------------------------------------------|
| `-i, --indir INDIR`            | Directory containing scanned images       |
| `-I, --inpdf INPDF`            | Input PDF file                            |
| `-o, --outdir OUTDIR`          | Directory for processed images            |
| `-O, --outpdf OUTPDF`          | Output PDF filepath                       |
| `-L, --language LANGS`         | OCR language(s) (e.g., eng+hin)           |
| `-c, --crop`                   | Enable automatic cropping                 |
| `-D, --deskew`                 | Enable deskewing                          |
| `-t, --ocr`                    | Apply OCR while creating PDF              |
| `-r, --reduce FACTOR`          | Resize images by factor                   |
| `-m, --maxcontours MAXCONTOURS`| Maximum contours to analyze (default: 5)  |
| `-x, --xmax XMAX`              | Horizontal line limit in pixels           |
| `-y, --ymax YMAX`              | Vertical line limit in pixels             |
| `-d, --drawcontours`           | Draw contours only (for debugging)        |
| `-g, --gray`                   | Convert to grayscale only                 |
| `-p, --pagenums`               | Process only specified pages              |
| `-l, --loglevel`               | Set log level                             |
| `-f, --logfile`                | Specify log file                          |

## RepuBUI Web Interface

RepuBUI provides a user-friendly web interface to the REPUB functionality, making document processing accessible without command-line knowledge.

### Setup

1. **Ensure REPUB is installed** as described in the installation section.

2. **Set up the web interface:**

   ```bash
   # Navigate to the RepuBUI directory
   cd repub/repubui
   
   # Create and activate a virtual environment
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   
   # Install dependencies
   pip install -r requirements.txt
   
   # Set up the database
   python manage.py makemigrations repub_interface
   python manage.py migrate
   
   # Create an admin user (optional)
   python manage.py createsuperuser
   
   # Start the development server
   python manage.py runserver
   ```

3. **Access the web interface** at http://127.0.0.1:8000

### Features

- **User-Friendly Interface**: Upload and process documents through an intuitive web UI
- **Flexible Input Formats**: Process PDFs or ZIP files containing images
- **Comprehensive Processing Options**:
  - OCR language selection with multi-language support
  - Automatic cropping and deskewing
  - Text layer generation
  - Dewarping and rotation options
  - Image resolution adjustment
- **Real-Time Status Updates**: Monitor processing progress
- **Interactive Review**: Examine and adjust processing results before finalizing
- **Batch Processing**: Handle multiple documents efficiently

### Directory Structure

```
repub/repubui/
├── manage.py              # Django management script
├── repubui/               # Main project directory
│   ├── settings.py        # Project settings
│   ├── urls.py            # Main URL configuration
│   └── wsgi.py            # WSGI configuration
├── repub_interface/       # Main application
│   ├── models.py          # Database models
│   ├── views.py           # View functions
│   ├── urls.py            # URL patterns
│   └── templates/         # HTML templates
└── templates/             # Global templates
```

### Media Storage

The application automatically creates and manages the following directories under `repub/repubui/media/`:

- `uploads/`: Stores uploaded document files
- `processed/`: Contains processed output files
- `thumbnails/`: Holds generated image previews

## Troubleshooting

If you encounter database errors when running the server:
1. Verify you've created migrations: `python manage.py makemigrations repub_interface`
2. Ensure migrations are applied: `python manage.py migrate`
3. Check directory permissions for media storage folders
4. Review the log file for specific error messages

