# REPUB Python API Client

A Python client library for submitting document processing jobs to REPUB UI using Django REST Framework token authentication.

## Features

- 🔐 **Token-based authentication** using Django REST Framework tokens
- 📄 **Document processing** for PDF files and image archives (ZIP)
- 🔄 **Job monitoring** with real-time status updates
- ⬇️ **Automatic downloads** of processed results
- 🚀 **Simple API** with both low-level and high-level methods
- 🔧 **Configurable processing options** (OCR, cropping, deskewing, etc.)
- 📦 **Batch processing** support for multiple documents with CSV and text file formats
- 🌐 **Multi-language support** with per-file language configuration
- ⚡ **Parallel processing** for batch operations

## Installation

### Requirements

- Python 3.7+
- `requests` library

```bash
pip install requests
```

### Setup

1. Copy the `repub_client.py` file to your project
2. Import the `REPUBClient` class

## Quick Start

### 1. Get Your API Token

Visit your REPUB UI instance and navigate to your user menu → "API Token" to generate your authentication token.

### 2. Basic Usage

```python
from repub_client import REPUBClient

# Initialize client
client = REPUBClient('http://localhost:8000', 'your_token_here')

# Process a document (complete workflow)
result = client.process_document(
    file_path='document.pdf',
    title='My Document',
    crop=True,
    deskew=True,
    ocr=True
)

if result['success']:
    print(f"Job completed! Result saved to: {result.get('output_path')}")
else:
    print(f"Error: {result['message']}")
```

### 3. Environment Variable Setup

For security, store your token as an environment variable:

```bash
export REPUB_TOKEN="your_token_here"
```

```python
import os
from repub_client import REPUBClient

client = REPUBClient('http://localhost:8000', os.environ['REPUB_TOKEN'])
```

## API Reference

### REPUBClient Class

#### `__init__(base_url, token, logger=None)`

Initialize the REPUB client.

**Parameters:**
- `base_url` (str): Base URL of the REPUB service (e.g., 'http://localhost:8000')
- `token` (str): REST framework authentication token
- `logger` (logging.Logger, optional): Custom logger instance

#### `submit_job(file_path, **options)`

Submit a document processing job.

**Parameters:**
- `file_path` (str|Path): Path to the file to process
- `title` (str, optional): Job title
- `input_type` (str): 'pdf' or 'images' (default: 'images')
- `language` (str): OCR language code (default: 'eng')
- `crop` (bool): Enable auto-cropping (default: True)
- `deskew` (bool): Enable deskewing (default: True)
- `ocr` (bool): Enable OCR (default: True)
- `dewarp` (bool): Enable dewarping (default: False)
- `draw_contours` (bool): Draw contours for debugging (default: False)
- `gray` (bool): Convert to grayscale (default: False)
- `rotate_type` (str): 'vertical', 'horizontal', or 'overall' (default: 'vertical')
- `reduce_factor` (float): Image scaling factor (default: 0.2)
- `xmaximum` (int): X maximum for line detection (default: 30)
- `ymax` (int): Y maximum for line detection (default: 60)
- `maxcontours` (int): Maximum contours to analyze (default: 5)

**Returns:**
Dictionary with job submission result:
```python
{
    'success': True,
    'job_id': 'uuid-string',
    'message': 'Job submitted successfully',
    'redirect_url': '/job/uuid-string/'
}
```

#### `get_job_status(job_id)`

Get the current status of a processing job.

**Parameters:**
- `job_id` (str): UUID of the job

**Returns:**
Dictionary with job status information.

#### `wait_for_completion(job_id, timeout=3600, poll_interval=10)`

Wait for a job to complete.

**Parameters:**
- `job_id` (str): UUID of the job
- `timeout` (int): Maximum time to wait in seconds (default: 1 hour)
- `poll_interval` (int): Time between status checks in seconds (default: 10s)

**Returns:**
Dictionary with final job status.

#### `download_result(job_id, output_path=None)`

Download the completed job result.

**Parameters:**
- `job_id` (str): UUID of the job
- `output_path` (str|Path, optional): Path to save the result file

**Returns:**
Boolean indicating success.

#### `process_document(file_path, output_path=None, wait_for_completion=True, auto_download=True, **kwargs)`

Complete workflow: submit job, wait for completion, and download result.

**Parameters:**
- `file_path` (str|Path): Path to the file to process
- `output_path` (str|Path, optional): Path to save the result
- `wait_for_completion` (bool): Whether to wait for job completion (default: True)
- `auto_download` (bool): Whether to automatically download the result (default: True)
- `**kwargs`: Additional arguments for `submit_job()`

**Returns:**
Dictionary with complete processing result.

#### `process_batch_from_file(batch_file, output_dir=None, max_workers=4, wait_for_completion=True, auto_download=False, **kwargs)`

Process multiple files specified in a batch file using parallel processing.

**Supports two file formats:**
1. **Text file**: One file path per line
2. **CSV file**: With 'title', 'file_path', and optional 'language' columns

**Parameters:**
- `batch_file` (str|Path): Path to text or CSV file containing file information
- `output_dir` (str|Path, optional): Directory to save results
- `max_workers` (int): Maximum number of parallel workers (default: 4)
- `wait_for_completion` (bool): Whether to wait for all jobs to complete (default: True)
- `auto_download` (bool): Whether to automatically download results (default: False)
- `**kwargs`: Additional arguments for `submit_job()`

**Returns:**
Dictionary with batch processing results including success/failure counts and detailed results for each file.

## Batch Processing

### CSV Format Batch Processing

Create a CSV file with columns for title, file path, and optional language:

**batch_files.csv:**
```csv
title,file_path,language
"Meeting Notes Q4",/path/to/meeting.pdf,eng
"Rapport Financier",/path/to/rapport.pdf,fra
"Technisches Handbuch",/path/to/manual.pdf,deu
"Research Paper",/path/to/research.pdf,eng
"Document without language",/path/to/document.pdf,
```

**Python usage:**
```python
from repub_client import REPUBClient

client = REPUBClient('http://localhost:8000', 'your_token_here')

# Process all files from CSV
result = client.process_batch_from_file(
    batch_file='batch_files.csv',
    output_dir='./results',
    max_workers=4,
    wait_for_completion=True,
    auto_download=True,
    crop=True,
    deskew=True,
    ocr=True
)

# Print summary
print(f"Processed {result['total_files']} files")
print(f"Successful: {result['successful_submissions']}")
print(f"Completed: {result['completed_jobs']}")
```

### Text Format Batch Processing

Create a text file with one file path per line:

**batch_files.txt:**
```
/path/to/document1.pdf
/path/to/document2.pdf
/path/to/images.zip
# Comments start with #
/path/to/another_document.pdf
```

**Python usage:**
```python
result = client.process_batch_from_file(
    batch_file='batch_files.txt',
    output_dir='./results',
    max_workers=2,
    wait_for_completion=True,
    auto_download=True
)
```

## Command Line Usage

The client can be used from the command line with enhanced batch processing support:

### Single File Processing
```bash
# Basic usage
python repub_client.py --token YOUR_TOKEN --file document.pdf

# With custom output and title
python repub_client.py --token YOUR_TOKEN --file document.pdf --output result.pdf --title "My Document"

# Submit without waiting and enable download
python repub_client.py --token YOUR_TOKEN --file document.pdf --no-wait --download
```

### Batch Processing
```bash
# Process files from text file
python repub_client.py --token YOUR_TOKEN --batch-file files.txt --output ./results

# Process files from CSV file with parallel workers
python repub_client.py --token YOUR_TOKEN --batch-file batch.csv --output ./results --max-workers 6

# Batch processing with automatic download
python repub_client.py --token YOUR_TOKEN --batch-file batch.csv --output ./results --download --max-workers 4

# With custom logging
python repub_client.py --token YOUR_TOKEN --batch-file batch.csv --logfile batch.log --log-level DEBUG
```

### Command Line Options
- `--file`: Single file to process
- `--batch-file`: Text file (one path per line) or CSV file (with title, file_path, language columns)
- `--output`: Output file path (single file) or output directory (batch)
- `--max-workers`: Maximum parallel workers for batch processing (default: 4)
- `--title`: Job title (single file only)
- `--no-wait`: Don't wait for job completion
- `--download`: Automatically download results when completed
- `--logfile`: Log file path (optional)
- `--log-level`: Logging level (DEBUG, INFO, WARNING, ERROR)

## Usage Examples

### Advanced Single Document Processing

```python
from repub_client import REPUBClient

client = REPUBClient('http://localhost:8000', 'your_token_here')

# High-quality processing with custom settings
result = client.process_document(
    file_path='scanned_book.pdf',
    title='Book Digitization',
    language='eng',
    crop=True,
    deskew=True,
    ocr=True,
    dewarp=True,          # Advanced page dewarping
    reduce_factor=0.8,    # Scale to 80% for faster processing
    maxcontours=10,       # Analyze more contours for better cropping
    output_path='digitized_book.pdf',
    wait_for_completion=True,
    auto_download=True
)

if result['success'] and result.get('downloaded'):
    print(f"✓ Book processed and saved to: {result['output_path']}")
else:
    print(f"✗ Error: {result.get('message', 'Unknown error')}")
```

### Parallel Batch Processing with Custom Settings

```python
import logging
from repub_client import REPUBClient

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('batch_processor')

client = REPUBClient('http://localhost:8000', 'your_token_here', logger=logger)

# Process batch with high-quality settings
result = client.process_batch_from_file(
    batch_file='documents.csv',  # CSV with title, file_path, language columns
    output_dir='./processed_documents',
    max_workers=6,              # Process 6 files in parallel
    wait_for_completion=True,
    auto_download=True,
    # Processing options applied to all files
    crop=True,
    deskew=True,
    ocr=True,
    dewarp=False,
    reduce_factor=0.5,          # Faster processing
    maxcontours=8
)

# Print detailed results
print("\nBatch Processing Results:")
print(f"  Total files: {result['total_files']}")
print(f"  Successful submissions: {result['successful_submissions']}")
print(f"  Completed jobs: {result['completed_jobs']}")
print(f"  Failed jobs: {result['failed_jobs']}")

if result['errors']:
    print(f"\nErrors ({len(result['errors'])}):")
    for error in result['errors']:
        print(f"  - {error['file_path']} ({error.get('title', 'No title')}): {error['error']}")

# Process results
for item in result['results']:
    if item['result'].get('success') and item['result'].get('downloaded'):
        print(f"✓ Processed: {item['title']} -> {item['result'].get('output_path')}")
```

### Multi-Language Document Processing

```python
# Create CSV file for multi-language processing
csv_content = """title,file_path,language
"English Manual",/docs/manual_en.pdf,eng
"French Guide",/docs/guide_fr.pdf,fra
"German Instructions",/docs/anweisungen_de.pdf,deu
"Spanish Tutorial",/docs/tutorial_es.pdf,spa
"Italian Documentation",/docs/documentazione_it.pdf,ita
"""

with open('multilang_batch.csv', 'w') as f:
    f.write(csv_content)

# Process with language-specific OCR
result = client.process_batch_from_file(
    batch_file='multilang_batch.csv',
    output_dir='./multilang_results',
    max_workers=3,
    wait_for_completion=True,
    auto_download=True,
    crop=True,
    deskew=True,
    ocr=True
)
```

### Monitoring and Error Handling

```python
import time
from repub_client import REPUBClient

client = REPUBClient('http://localhost:8000', 'your_token_here')

# Submit multiple jobs and monitor
job_ids = []
files = ['doc1.pdf', 'doc2.pdf', 'doc3.pdf']

# Submit all jobs
for file_path in files:
    result = client.submit_job(
        file_path=file_path,
        title=f"Batch: {Path(file_path).stem}",
        crop=True,
        ocr=True
    )
    if result['success']:
        job_ids.append((result['job_id'], file_path))
        print(f"✓ Submitted: {file_path} -> {result['job_id']}")
    else:
        print(f"✗ Failed to submit: {file_path} -> {result['message']}")

# Monitor all jobs
completed = []
failed = []

while job_ids:
    for job_id, file_path in job_ids[:]:  # Create a copy of the list
        status = client.get_job_status(job_id)
        current_status = status.get('status', 'unknown')
        
        if current_status == 'completed':
            if client.download_result(job_id, f"result_{Path(file_path).stem}.pdf"):
                completed.append((job_id, file_path))
                print(f"✓ Completed and downloaded: {file_path}")
            else:
                print(f"✓ Completed but download failed: {file_path}")
            job_ids.remove((job_id, file_path))
            
        elif current_status == 'failed':
            failed.append((job_id, file_path, status.get('message', 'Unknown error')))
            print(f"✗ Failed: {file_path} -> {status.get('message')}")
            job_ids.remove((job_id, file_path))
        else:
            print(f"⏳ Processing: {file_path} -> {current_status}")
    
    if job_ids:  # If there are still jobs running
        time.sleep(10)  # Wait 10 seconds before checking again

print(f"\nFinal Summary:")
print(f"  Completed: {len(completed)}")
print(f"  Failed: {len(failed)}")
```

## Processing Options Explained

### File Types
- **PDF files**: Automatically extracted to individual page images
- **ZIP archives**: Should contain image files (JPG, PNG, TIFF)

### Processing Steps
1. **Cropping** (`crop`): Automatically detect and crop page boundaries
2. **Deskewing** (`deskew`): Correct page rotation/skew
3. **Dewarping** (`dewarp`): Advanced page curvature correction
4. **OCR** (`ocr`): Optical Character Recognition to make text searchable

### Language Support
The `language` parameter supports Tesseract language codes:
- `eng` - English (default)
- `fra` - French  
- `deu` - German
- `spa` - Spanish
- `ita` - Italian
- `por` - Portuguese
- `rus` - Russian
- `chi_sim` - Chinese Simplified
- `jpn` - Japanese
- And many more...

### Performance Tuning
- `reduce_factor`: Scale images (0.5 = 50% size, faster processing)
- `maxcontours`: Number of contours to analyze for cropping (higher = more accurate, slower)
- `xmaximum`/`ymax`: Line detection thresholds for advanced processing
- `max_workers`: Number of parallel workers for batch processing (recommended: 2-8)

## CSV File Format Specification

### Required Columns
- `file_path`: Full path to the file to be processed

### Optional Columns  
- `title`: Job title (defaults to filename if empty)
- `language`: OCR language code (defaults to global default if empty)

### Example CSV Files

**Basic CSV:**
```csv
title,file_path
"Document 1",/path/to/doc1.pdf
"Document 2",/path/to/doc2.pdf
```

**Full CSV with languages:**
```csv
title,file_path,language
"English Report",/docs/report_en.pdf,eng
"French Report",/docs/rapport_fr.pdf,fra  
"German Report",/docs/bericht_de.pdf,deu
"Mixed Languages",/docs/mixed.pdf,eng
"Default Language",/docs/default.pdf,
```

**CSV with special characters:**
```csv
title,file_path,language
"Report with ""Quotes""",/docs/quoted.pdf,eng
"Report, with commas",/docs/commas.pdf,eng
"Résumé français",/docs/resume.pdf,fra
```

## Error Handling

The client provides detailed error information:

```python
result = client.submit_job('nonexistent.pdf')

if not result['success']:
    print(f"Error: {result['message']}")
    if 'status_code' in result:
        print(f"HTTP Status: {result['status_code']}")

# Batch processing errors
batch_result = client.process_batch_from_file('batch.csv')
if batch_result['errors']:
    for error in batch_result['errors']:
        print(f"File: {error['file_path']}")
        print(f"Title: {error.get('title', 'N/A')}")
        print(f"Language: {error.get('language', 'N/A')}")
        print(f"Error: {error['error']}")
```

Common errors:
- **401 Unauthorized**: Invalid or expired token
- **404 Not Found**: File not found or invalid job ID  
- **413 Request Entity Too Large**: File too large
- **500 Internal Server Error**: Server processing error
- **CSV Format Error**: Invalid CSV structure or missing required columns
- **File Not Found**: File path in batch file doesn't exist

## Security Notes

- 🔒 **Never commit tokens** to version control
- 🔒 **Use environment variables** for token storage
- 🔒 **Rotate tokens regularly** via the web interface
- 🔒 **Use HTTPS** in production environments
- 🔒 **Validate file paths** in batch files to prevent path traversal
- 🔒 **Sanitize CSV input** when accepting user-provided batch files

## Performance Recommendations

### Batch Processing
- Use 4-8 parallel workers for optimal performance
- Consider system resources when setting `max_workers`
- Use `auto_download=False` for large batches to prevent storage issues
- Monitor memory usage with large files or high worker counts

### File Processing
- Use `reduce_factor < 1.0` for faster processing of high-resolution images
- Enable `dewarp` only when necessary (adds significant processing time)
- Adjust `maxcontours` based on document complexity (5-10 for most documents)

### Network Optimization
- Process files in the same network/data center as the REPUB server
- Use appropriate timeouts for large files
- Consider chunked uploads for very large files

## Troubleshooting

### Authentication Issues
```python
# Test token validity
try:
    result = client.get_job_status('test-id')
    if result.get('success') == False and 'authentication' in result.get('message', '').lower():
        print("Token is invalid or expired")
except Exception as e:
    print(f"Authentication test failed: {e}")
```

### Batch Processing Issues
```python
# Validate CSV before processing
import csv
from pathlib import Path

def validate_csv_batch_file(csv_file):
    errors = []
    
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            if 'file_path' not in reader.fieldnames:
                return ["CSV must contain 'file_path' column"]
            
            for row_num, row in enumerate(reader, 2):
                file_path = row.get('file_path', '').strip()
                if not file_path:
                    errors.append(f"Row {row_num}: Empty file_path")
                    continue
                    
                if not Path(file_path).exists():
                    errors.append(f"Row {row_num}: File not found: {file_path}")
                    
    except Exception as e:
        errors.append(f"CSV parsing error: {str(e)}")
    
    return errors

# Usage
errors = validate_csv_batch_file('batch.csv')
if errors:
    print("CSV validation errors:")
    for error in errors:
        print(f"  - {error}")
else:
    print("CSV file is valid")
```

### Network Issues
```python
# Add timeout and retries
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

def create_robust_session():
    session = requests.Session()
    
    # Configure retries
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Set timeout
    session.request = functools.partial(session.request, timeout=60)
    
    return session

# Use with client
client = REPUBClient('http://localhost:8000', 'your_token_here')
client.session = create_robust_session()
```

## Integration Examples

### Django Integration with Batch Processing

```python
# In your Django views
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from repub_client import REPUBClient
import tempfile
import os

@csrf_exempt
def batch_process_view(request):
    if request.method == 'POST':
        batch_file = request.FILES.get('batch_file')
        if not batch_file:
            return JsonResponse({'error': 'No batch file provided'}, status=400)
        
        # Save batch file temporarily
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
            for chunk in batch_file.chunks():
                f.write(chunk)
            temp_batch_path = f.name
        
        try:
            # Process batch
            client = REPUBClient(settings.REPUB_URL, settings.REPUB_TOKEN)
            result = client.process_batch_from_file(
                batch_file=temp_batch_path,
                max_workers=4,
                wait_for_completion=True,
                auto_download=False  # Handle downloads separately
            )
            
            # Clean up
            os.unlink(temp_batch_path)
            
            return JsonResponse({
                'success': True,
                'total_files': result['total_files'],
                'completed_jobs': result['completed_jobs'],
                'failed_jobs': result['failed_jobs'],
                'job_ids': [r['result'].get('job_id') for r in result['results'] if r['result'].get('success')]
            })
            
        except Exception as e:
            os.unlink(temp_batch_path)
            return JsonResponse({'error': str(e)}, status=500)
```

### Flask Integration with CSV Upload

```python
from flask import Flask, request, jsonify, send_file
from repub_client import REPUBClient
import tempfile
import os
import csv

app = Flask(__name__)
client = REPUBClient('http://localhost:8000', os.environ['REPUB_TOKEN'])

@app.route('/process_batch', methods=['POST'])
def process_batch():
    if 'batch_file' not in request.files:
        return jsonify({'error': 'No batch file provided'}), 400
    
    batch_file = request.files['batch_file']
    max_workers = int(request.form.get('max_workers', 4))
    
    # Save batch file temporarily
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
        batch_file.save(f.name)
        temp_path = f.name
    
    try:
        result = client.process_batch_from_file(
            batch_file=temp_path,
            max_workers=max_workers,
            wait_for_completion=True,
            auto_download=False
        )
        
        return jsonify({
            'success': True,
            'summary': {
                'total_files': result['total_files'],
                'successful_submissions': result['successful_submissions'],
                'completed_jobs': result['completed_jobs'],
                'failed_jobs': result['failed_jobs']
            },
            'results': result['results']
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        os.unlink(temp_path)

@app.route('/download_batch_results', methods=['POST'])
def download_batch_results():
    job_ids = request.json.get('job_ids', [])
    
    # Create temporary directory for downloads
    with tempfile.TemporaryDirectory() as temp_dir:
        downloaded_files = []
        
        for job_id in job_ids:
            output_path = os.path.join(temp_dir, f"{job_id}.pdf")
            if client.download_result(job_id, output_path):
                downloaded_files.append(output_path)
        
        if downloaded_files:
            # Create zip of all results
            import zipfile
            zip_path = os.path.join(temp_dir, 'batch_results.zip')
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for file_path in downloaded_files:
                    zipf.write(file_path, os.path.basename(file_path))
            
            return send_file(zip_path, as_attachment=True, download_name='batch_results.zip')
        else:
            return jsonify({'error': 'No files could be downloaded'}), 404
```

## Contributing

To extend the client:

1. Add new methods to the `REPUBClient` class
2. Update error handling and logging
3. Add comprehensive documentation and examples  
4. Test with various file types, batch sizes, and network conditions
5. Update this README with new features

## Support

- 📧 Check server logs for detailed error information
- 🐛 Report issues with specific error messages and batch file samples
- 📚 Refer to REPUB UI documentation for processing options
- 🔧 Use the web interface to verify expected behavior
- 📊 Monitor system resources during batch processing
- 🚀 Test batch processing with small sets before large-scale operations