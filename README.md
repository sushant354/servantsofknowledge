For REPUB project
==================
First install the dependencies ..

symlink repub in your site packages
```
ln -s <maindirectory>/servantsofknowledge/repub <python_site_package_dir/repub

```
Install dependencies
```
pip install -r requirements.txt 
```
Install tesseract. On debian systems:
```
apt install tesseract-ocr tesseract-ocr-all
```
process_raw.py is the main program to transform scanned images to a PDF with text layer

To convert Internet Archive images to PDF with text layer with cropping and language "eng+asm"
```
python process_raw.py -i <ia_scan_dir> -O <output_file_path.pdf> -c -L eng+asm
```
To convert a PDF file to a PDF file with text layer with cropping and language "eng+kan". Remove "-c" if no need to crop.
```
python process_raw.py -I <input_file_path.pdf> -O <output_file_path.pdf> -c -L eng+kan
```

Options
```
usage: process_raw.py [-h] [-i INDIR] [-I INPDF] [-o OUTDIR] [-O OUTPDF]
                      [-l LOGLEVEL] [-L LANGS] [-f LOGFILE] [-m MAXCONTOURS]
                      [-x XMAX] [-y YMAX] [-d] [-p [PAGENUMS ...]] [-g] [-c]
                      [-D] [-r FACTOR] [-t]

For processing scanned book pages

options:
  -h, --help            show this help message and exit
  -i INDIR, --indir INDIR
                        Filepath to scanned images directory
  -I INPDF, --inpdf INPDF
                        Input PDF File
  -o OUTDIR, --outdir OUTDIR
                        Filepath to processed directory
  -O OUTPDF, --outpdf OUTPDF
                        Output PDF filepath
  -l LOGLEVEL, --loglevel LOGLEVEL
                        debug level
  -L LANGS, --language LANGS
                        language for tesseract
  -f LOGFILE, --logfile LOGFILE
                        log file
  -m MAXCONTOURS, --maxcontours MAXCONTOURS
                        max number of contours to be examined
  -x XMAX, --xmax XMAX  horizontal line limits in pixels
  -y YMAX, --ymax YMAX  vertical line limits in pixels
  -d, --drawcontours    draw contours only on the image
  -p [PAGENUMS ...], --pagenums [PAGENUMS ...]
                        pagenums that should only be processed
  -g, --gray            only gray the image and threshold it
  -c, --crop            crop the scanned image
  -D, --deskew          detect the skew and deskew
  -r FACTOR, --reduce FACTOR
                        reduce the image to factor
  -t, --ocr             do ocr while making the PDF

```

RepuBUI - Web Interface
======================

RepuBUI is a web-based interface for the REPUB project that provides a user-friendly way to process documents. It offers all the functionality of the command-line tool through an intuitive web interface.

Installation & Setup
------------------

1. Make sure you have completed the REPUB installation steps above first.

2. Navigate to the RepuBUI directory and create a virtual environment:
```bash
cd repub/repubui
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
```

3. Install additional dependencies for RepuBUI:
```bash
pip install -r requirements.txt
```

4. Set up the database:
```bash
# Create initial migrations for the repub_interface app
python manage.py makemigrations repub_interface

# Apply all migrations (both Django's default and repub_interface)
python manage.py migrate
```

5. Create a superuser (optional, for admin access):
```bash
python manage.py createsuperuser
```

6. Start the development server:
```bash
python manage.py runserver
```

The web interface will be available at http://127.0.0.1:8000

Note: If you encounter any database errors when running the server, make sure you've:
1. Created the migrations for repub_interface using `python manage.py makemigrations repub_interface`
2. Applied all migrations using `python manage.py migrate`
3. Have proper write permissions in the project directory

Features
--------

- Upload PDF files or ZIP files containing images
- Configure processing options through a user-friendly interface:
  - OCR language selection
  - Cropping
  - Deskewing
  - OCR processing
  - Dewarping
  - Rotation options
  - Image reduction
- Real-time processing status updates
- Download processed PDFs
- Review and adjust page processing results
- Admin interface for job management

Directory Structure
-----------------

```
repub/repubui/
├── manage.py              # Django management script
├── repubui/              # Main project directory
│   ├── settings.py       # Project settings
│   ├── urls.py           # Main URL configuration
│   └── wsgi.py          # WSGI configuration
├── repub_interface/      # Main application
│   ├── models.py         # Database models
│   ├── views.py          # View functions
│   ├── urls.py          # URL patterns
│   └── templates/       # HTML templates
└── templates/           # Global templates
```

Media Storage
------------

The application stores files in the following directories under `repub/repubui/media/`:

- `uploads/`: Original uploaded files
- `processed/`: Processed output files
- `thumbnails/`: Generated thumbnails for preview

These directories are automatically created when needed.
