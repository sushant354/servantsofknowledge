# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

REPUB is a document processing system with both a command-line interface and web application. It processes scanned book pages and documents through cropping, deskewing, OCR, and PDF generation. The system consists of:

1. **Core Library (`repub/`)** - Image processing utilities and algorithms
2. **Django Web Application (`repubui/`)** - Web interface for document processing jobs
3. **Command-line Interface** - Direct processing via `process_raw.py`

## Architecture

### Core Processing Pipeline
- **Image Functions (`imgfuncs/`)**: Handles cropping, deskewing, line detection, and image utilities
- **Utilities (`utils/`)**: PDF operations, HTML processing, HOCR processing, XML operations, and directory scanning
- **Processing Engine**: Orchestrates the full pipeline from raw images to final PDF output

### Django Web Application Structure
- **Models**: `ProcessingJob` (main job entity) and `PageImage` (individual page handling)
- **Processing Flow**: Upload → Background Processing → Review Interface → Final Output
- **File Organization**: 
  - Uploads: `media/uploads/{job_id}/`
  - Processed: `media/processed/{job_id}/`
  - Thumbnails: `media/thumbnails/{job_id}/`

## Development Commands
Try and catch specific exemption. Do not wrap entire code ino a generic try/catch block.

### Django Web Application
```bash
# Navigate to Django project
cd repubui/

# Install dependencies (requires virtual environment activation)
pip install -r ../requirements.txt

# Database operations
python manage.py makemigrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver

# Collect static files (for production)
python manage.py collectstatic

# Access Django shell
python manage.py shell
```

### Command-line Processing
```bash
# Process images from directory
python process_raw.py -i input_dir -o output_dir --crop --deskew --ocr

# Process PDF input
python process_raw.py -I input.pdf -O output.pdf --crop --deskew --ocr

# Common processing options:
# --crop (-c): Auto-crop pages
# --deskew (-D): Correct page skew
# --ocr (-t): Perform OCR and create searchable PDF
# --language (-L): Set OCR language (default: eng)
# --reduce (-r): Scale images by factor (e.g., 0.5 for 50%)
# --maxcontours (-m): Max contours to examine for cropping (default: 5)
```

## Configuration

### Environment Variables (.env file in repubui/)
- `SECRET_KEY`: Django secret key
- `DEBUG`: Set to 'True' for development
- `DEPLOYMENT`: 'local' for SQLite, 'prod' for PostgreSQL
- `ALLOWED_HOSTS`: Comma-separated list of allowed hosts
- `DB_*`: PostgreSQL configuration (when DEPLOYMENT=prod)
- `POSTMARK_TOKEN`: Email service configuration

### Database Configuration
- **Local Development**: SQLite (default)
- **Production**: PostgreSQL with environment variables

## Key Processing Parameters

### Image Processing Options
- `crop`: Auto-detect and crop page boundaries
- `deskew`: Detect and correct page rotation
- `ocr`: Perform OCR using Tesseract
- `language`: OCR language code (eng, fra, deu, etc.)
- `dewarp`: Advanced page dewarping (experimental)
- `rotate_type`: Rotation detection method (vertical/horizontal/overall)
- `reduce_factor`: Image scaling factor
- `xmaximum/ymax`: Line detection thresholds
- `maxcontours`: Maximum contours to analyze for cropping

### Job Status Flow
1. `pending` → `processing` → `completed`
2. Alternative: `processing` → `reviewing` → `finalizing` → `completed`
3. Error state: `failed`

## File Structure Notes

- **Core library imports**: Use `from repub.module import function` syntax
- **Django app**: Uses Django 5.2 with REST framework
- **Media handling**: All uploads and processing occur in `media/` subdirectories
- **Logging**: Configured for both file and console output
- **Static files**: Django admin and REST framework assets included

## Testing

No specific test framework configured. Manual testing through:
1. Web interface at `http://localhost:8000`
2. Command-line processing with sample images
3. Django admin interface at `http://localhost:8000/admin`

## Common Workflows

1. **Web-based Processing**: Upload → Auto-process → Review individual pages → Finalize
2. **Batch Processing**: Use command-line interface with directory of images
3. **PDF Workflow**: Upload PDF → Extract pages → Process → Generate searchable PDF
4. **Manual Adjustments**: Use page editor for fine-tuning crop boundaries

## Dependencies

Key dependencies include OpenCV, Django, Tesseract (via pytesseract), pdf2image, and various image processing libraries. See `requirements.txt` for complete list.
