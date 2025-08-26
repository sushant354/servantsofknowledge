#!/usr/bin/env python3
"""
REPUB Python Client

A Python client for submitting document processing jobs to REPUB UI
using Django REST Framework token authentication.

Supports both single file processing and batch processing with parallel execution
using concurrent.futures for improved performance when processing multiple files.
"""

import requests
import json
import os
import time
import logging
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, Union, List
from pathlib import Path
import threading


class REPUBClient:
    """Python client for REPUB document processing service"""
    
    def __init__(self, base_url: str, token: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the REPUB client
        
        Args:
            base_url: Base URL of the REPUB service (e.g., 'http://localhost:8000')
            token: REST framework authentication token
        """
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.logger = logger or logging.getLogger(__name__)
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Token {token}',
            'User-Agent': 'REPUB-Python-Client/1.0'
        })
    
    
    def submit_job(self, 
                   file_path: Union[str, Path],
                   title: Optional[str] = None,
                   input_type: str = 'images',
                   language: str = 'eng',
                   crop: bool = True,
                   deskew: bool = True,
                   ocr: bool = True,
                   dewarp: bool = False,
                   draw_contours: bool = False,
                   gray: bool = False,
                   rotate_type: str = 'vertical',
                   reduce_factor: float = 0.2,
                   xmaximum: int = 30,
                   ymax: int = 60,
                   maxcontours: int = 5) -> Dict[str, Any]:
        """
        Submit a document processing job
        
        Args:
            file_path: Path to the file to process (PDF or ZIP)
            title: Job title (optional, will use filename if not provided)
            input_type: Type of input ('pdf' or 'images')
            language: OCR language code (default: 'eng')
            crop: Enable auto-cropping (default: True)
            deskew: Enable deskewing (default: True)
            ocr: Enable OCR (default: True)
            dewarp: Enable dewarping (default: False)
            draw_contours: Draw contours for debugging (default: False)
            gray: Convert to grayscale (default: False)
            rotate_type: Rotation type ('vertical', 'horizontal', 'overall')
            reduce_factor: Image scaling factor (default: 1.0)
            xmaximum: X maximum for line detection (default: 0)
            ymax: Y maximum for line detection (default: 0)
            maxcontours: Maximum contours to analyze (default: 5)
            
        Returns:
            Dict containing job information or error details
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if title is None:
            title = file_path.stem
        
        # Prepare form data (mimic HTML form submission)
        data = {
            'title': title,
            'input_type': input_type,
            'language': language,
            'rotate_type': rotate_type,
            'reduce_factor': str(reduce_factor),
            'xmaximum': str(xmaximum),
            'ymax': str(ymax),
            'maxcontours': str(maxcontours),
        }
        
        # Add checkboxes only if True (like HTML forms do)
        if crop:
            data['crop'] = 'on'
        if deskew:
            data['deskew'] = 'on'
        if ocr:
            data['ocr'] = 'on'
        if dewarp:
            data['dewarp'] = 'on'
        if draw_contours:
            data['draw_contours'] = 'on'
        if gray:
            data['gray'] = 'on'
        
        # Prepare file upload
        with open(file_path, 'rb') as f:
            files = {'input_file': (file_path.name, f, 'application/octet-stream')}
            
            try:
                response = self.session.post(
                    f"{self.base_url}/",
                    data=data,
                    files=files,
                    allow_redirects=False  # Don't follow redirects to capture job ID
                )
                
                if response.status_code == 302:  # Redirect indicates success
                    # Extract job ID from redirect URL
                    location = response.headers.get('Location', '')
                    if '/job/' in location:
                        job_id = location.split('/job/')[1].split('/')[0]
                        return {
                            'success': True,
                            'job_id': job_id,
                            'message': 'Job submitted successfully',
                            'redirect_url': location
                        }
                
                # If we get here, there was an error
                return {
                    'success': False,
                    'status_code': response.status_code,
                    'message': 'Job submission failed',
                    'error': response.text[:500]  # First 500 chars of error
                }
                
            except Exception as e:
                return {
                    'success': False,
                    'message': f'Request failed: {str(e)}'
                }
    
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of a processing job
        
        Args:
            job_id: UUID of the job
            
        Returns:
            Dict containing job status information
        """
        try:
            response = self.session.get(f"{self.base_url}/job/{job_id}/status/")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {
                'success': False,
                'message': f'Failed to get job status: {str(e)}'
            }
    
    def wait_for_completion(self, job_id: str, 
                          timeout: int = 3600, 
                          poll_interval: int = 10) -> Dict[str, Any]:
        """
        Wait for a job to complete
        
        Args:
            job_id: UUID of the job
            timeout: Maximum time to wait in seconds (default: 1 hour)
            poll_interval: Time between status checks in seconds (default: 10s)
            
        Returns:
            Dict containing final job status
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.get_job_status(job_id)
            
            if not status.get('success', True):  # API error
                return status
            
            job_status = status.get('status', 'unknown')
            
            if job_status in ['completed', 'failed']:
                return status
            
            self.logger.info(f"Job {job_id} status: {job_status}")
            time.sleep(poll_interval)
        
        return {
            'success': False,
            'message': f'Job did not complete within {timeout} seconds',
            'status': 'timeout'
        }
    
    def download_result(self, job_id: str, output_path: Optional[Union[str, Path]] = None) -> bool:
        """
        Download the completed job result
        
        Args:
            job_id: UUID of the job
            output_path: Path to save the result file (optional)
            
        Returns:
            True if download was successful, False otherwise
        """
        try:
            response = self.session.get(f"{self.base_url}/job/{job_id}/download/")
            response.raise_for_status()
            
            if output_path is None:
                # Extract filename from Content-Disposition header or use job_id
                disposition = response.headers.get('Content-Disposition', '')
                if 'filename=' in disposition:
                    filename = disposition.split('filename=')[1].strip('"')
                else:
                    filename = f"{job_id}_result.pdf"
                output_path = Path(filename)
            else:
                output_path = Path(output_path)
            
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            self.logger.info(f"Result downloaded to: {output_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to download result: {str(e)}")
            return False
    
    def process_document(self, 
                        file_path: Union[str, Path],
                        output_path: Optional[Union[str, Path]] = None,
                        wait_for_completion: bool = True,
                        auto_download: bool = True,
                        **kwargs) -> Dict[str, Any]:
        """
        Complete workflow: submit job, wait for completion, and optionally download result
        
        Args:
            file_path: Path to the file to process
            output_path: Path to save the result (optional)
            wait_for_completion: Whether to wait for job completion (default: True)
            auto_download: Whether to automatically download the result when completed (default: True)
            **kwargs: Additional arguments for submit_job()
            
        Returns:
            Dict containing the complete processing result
        """
        self.logger.info(f"Submitting job for: {file_path}")
        result = self.submit_job(file_path, **kwargs)
        
        if not result.get('success'):
            return result
        
        job_id = result['job_id']
        self.logger.info(f"Job submitted successfully. Job ID: {job_id}")
        
        if wait_for_completion:
            self.logger.info("Waiting for job completion...")
            status = self.wait_for_completion(job_id)
            
            if status.get('status') == 'completed':
                self.logger.info("Job completed successfully!")
                if auto_download:
                    if self.download_result(job_id, output_path):
                        result['downloaded'] = True
                        result['output_path'] = str(output_path) if output_path else None
                    else:
                        result['downloaded'] = False
                else:
                    result['downloaded'] = False
                    self.logger.info("Auto-download disabled, result not downloaded")
            else:
                self.logger.error(f"Job did not complete successfully: {status.get('message', 'Unknown error')}")
                result.update(status)
        
        return result

    def process_batch_from_file(self, 
                                batch_file: Union[str, Path], 
                                output_dir: Optional[Union[str, Path]] = None,
                                max_workers: int = 4,
                                wait_for_completion: bool = True,
                                auto_download: bool = False,
                                **kwargs) -> Dict[str, Any]:
        """
        Process multiple files specified in a batch file using parallel processing
        
        Supports two file formats:
        1. Text file with file paths (one per line)
        2. CSV file with 'title', 'file_path', and optional 'language' columns
        
        Args:
            batch_file: Path to text or CSV file containing file information
            output_dir: Directory to save results (optional)
            max_workers: Maximum number of parallel workers (default: 4)
            wait_for_completion: Whether to wait for all jobs to complete (default: True)
            auto_download: Whether to automatically download results (default: False)
            **kwargs: Additional arguments for submit_job()
            
        Returns:
            Dict containing batch processing results
        """
        batch_file = Path(batch_file)
        
        if not batch_file.exists():
            raise FileNotFoundError(f"Batch file not found: {batch_file}")
        
        # Determine file format and read file data
        file_data = []
        try:
            if batch_file.suffix.lower() == '.csv':
                # CSV format with title and file_path columns
                with open(batch_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    
                    # Check for required columns
                    if 'file_path' not in reader.fieldnames:
                        raise ValueError("CSV file must contain a 'file_path' column")
                    
                    for row_num, row in enumerate(reader, 2):  # Start at 2 (header is row 1)
                        file_path_str = row.get('file_path', '').strip()
                        if not file_path_str:
                            self.logger.warning(f"Row {row_num}: Empty file_path, skipping")
                            continue
                            
                        file_path = Path(file_path_str)
                        if file_path.exists():
                            title = row.get('title', '').strip() or file_path.stem
                            language = row.get('language', '').strip() or None  # Use None if empty/missing
                            file_data.append({
                                'file_path': file_path,
                                'title': title,
                                'language': language
                            })
                        else:
                            self.logger.warning(f"Row {row_num}: File not found: {file_path_str}")
            else:
                # Text format with file paths (one per line)
                with open(batch_file, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if line and not line.startswith('#'):  # Skip empty lines and comments
                            file_path = Path(line)
                            if file_path.exists():
                                file_data.append({
                                    'file_path': file_path,
                                    'title': file_path.stem,  # Use filename as title
                                    'language': None  # No language specified in text format
                                })
                            else:
                                self.logger.warning(f"Line {line_num}: File not found: {line}")
        except Exception as e:
            raise Exception(f"Failed to read batch file: {str(e)}")
        
        if not file_data:
            raise ValueError("No valid file paths found in batch file")
        
        self.logger.info(f"Processing {len(file_data)} files from batch with {max_workers} workers")
        
        # Create output directory if specified
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
        
        results = {
            'total_files': len(file_data),
            'successful_submissions': 0,
            'failed_submissions': 0,
            'completed_jobs': 0,
            'failed_jobs': 0,
            'results': [],
            'errors': []
        }
        
        # Process files in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all jobs
            future_to_data = {}
            for file_info in file_data:
                file_path = file_info['file_path']
                title = file_info['title']
                language = file_info['language']
                
                # Generate output path if output_dir is specified
                file_output_path = None
                if output_dir and auto_download:
                    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
                    if not safe_title:
                        safe_title = file_path.stem
                    file_output_path = output_dir / f"{safe_title}_processed.pdf"
                
                # Add title and language to kwargs for this specific file
                file_kwargs = kwargs.copy()
                file_kwargs['title'] = title
                if language:  # Only set language if it's specified in CSV
                    file_kwargs['language'] = language
                
                future = executor.submit(
                    self._process_single_file,
                    file_path,
                    file_output_path,
                    wait_for_completion,
                    auto_download,
                    **file_kwargs
                )
                future_to_data[future] = file_info
            
            # Collect results as they complete
            for future in as_completed(future_to_data):
                file_info = future_to_data[future]
                file_path = file_info['file_path']
                title = file_info['title']
                language = file_info['language']
                
                try:
                    result = future.result()
                    result_entry = {
                        'file_path': str(file_path),
                        'title': title,
                        'result': result
                    }
                    if language:
                        result_entry['language'] = language
                    results['results'].append(result_entry)
                    
                    if result.get('success'):
                        results['successful_submissions'] += 1
                        if result.get('status') == 'completed':
                            results['completed_jobs'] += 1
                        elif result.get('status') == 'failed':
                            results['failed_jobs'] += 1
                    else:
                        results['failed_submissions'] += 1
                        
                except Exception as e:
                    lang_info = f" (language: {language})" if language else ""
                    error_msg = f"Error processing {file_path} (title: {title}{lang_info}): {str(e)}"
                    self.logger.error(error_msg)
                    error_entry = {
                        'file_path': str(file_path),
                        'title': title,
                        'error': str(e)
                    }
                    if language:
                        error_entry['language'] = language
                    results['errors'].append(error_entry)
                    results['failed_submissions'] += 1
        
        # Summary
        self.logger.info(f"Batch processing complete:")
        self.logger.info(f"  Total files: {results['total_files']}")
        self.logger.info(f"  Successful submissions: {results['successful_submissions']}")
        self.logger.info(f"  Failed submissions: {results['failed_submissions']}")
        if wait_for_completion:
            self.logger.info(f"  Completed jobs: {results['completed_jobs']}")
            self.logger.info(f"  Failed jobs: {results['failed_jobs']}")
        
        return results
    
    def _process_single_file(self, 
                            file_path: Path, 
                            output_path: Optional[Path], 
                            wait_for_completion: bool,
                            auto_download: bool,
                            **kwargs) -> Dict[str, Any]:
        """
        Process a single file (internal method for parallel execution)
        
        Args:
            file_path: Path to the file to process
            output_path: Path to save the result (optional)
            wait_for_completion: Whether to wait for job completion
            auto_download: Whether to automatically download the result
            **kwargs: Additional arguments for submit_job()
            
        Returns:
            Dict containing the processing result
        """
        thread_id = threading.current_thread().ident
        self.logger.info(f"[Thread-{thread_id}] Processing: {file_path}")
        
        try:
            result = self.process_document(
                file_path=file_path,
                output_path=output_path,
                wait_for_completion=wait_for_completion,
                auto_download=auto_download,
                **kwargs
            )
            
            if result.get('success'):
                self.logger.info(f"[Thread-{thread_id}] Successfully submitted: {file_path} (Job ID: {result.get('job_id')})")
            else:
                self.logger.error(f"[Thread-{thread_id}] Failed to submit: {file_path} - {result.get('message', 'Unknown error')}")
            
            return result
            
        except Exception as e:
            error_msg = f"Exception processing {file_path}: {str(e)}"
            self.logger.error(f"[Thread-{thread_id}] {error_msg}")
            return {
                'success': False,
                'message': error_msg,
                'file_path': str(file_path)
            }


def main():
    """Example usage of the REPUB client"""
    import argparse
    
    parser = argparse.ArgumentParser(description='REPUB Python Client')
    parser.add_argument('--url', default='http://localhost:8000', 
                       help='REPUB server URL (default: http://localhost:8000)')
    parser.add_argument('--token', required=True, 
                       help='REST framework authentication token')
    # File processing options (mutually exclusive)
    file_group = parser.add_mutually_exclusive_group(required=True)
    file_group.add_argument('--file', 
                           help='Path to single file to process')
    file_group.add_argument('--batch-file',
                           help='Path to text file (one file path per line) or CSV file (with title, file_path, and optional language columns)')
    
    parser.add_argument('--output', 
                       help='Output file path (single file) or output directory (batch)')
    parser.add_argument('--max-workers', type=int, default=4,
                       help='Maximum number of parallel workers for batch processing (default: 4)')
    parser.add_argument('--title', 
                       help='Job title (optional)')
    parser.add_argument('--language', default='eng',
                       help='OCR language code (default: eng). Examples: eng, fra, deu, spa, ita, etc.')
    parser.add_argument('--no-wait', action='store_true',
                       help='Don\'t wait for job completion')
    parser.add_argument('--download', action='store_true',
                       help='Automatically download result when completed')
    parser.add_argument('--logfile', 
                       help='Log file path (optional, logs to console if not specified)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], 
                       default='INFO', help='Logging level (default: INFO)')
    
    args = parser.parse_args()
    
    # Setup logging
    logger = logging.getLogger('repub_client')
    logger.setLevel(getattr(logging, args.log_level))
    
    # Remove existing handlers to avoid duplicates
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    if args.logfile:
        # Log to file
        file_handler = logging.FileHandler(args.logfile)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    else:
        # Log to console
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Create client with custom logger
    client = REPUBClient(args.url, args.token, logger=logger)
    
    # Process document(s)
    if args.file:
        # Single file processing
        result = client.process_document(
            file_path=args.file,
            output_path=args.output,
            title=args.title,
            language=args.language,
            wait_for_completion=not args.no_wait,
            auto_download=args.download
        )
        print(f"\nResult: {json.dumps(result, indent=2)}")
        
    elif args.batch_file:
        # Batch processing
        result = client.process_batch_from_file(
            batch_file=args.batch_file,
            output_dir=args.output,
            max_workers=args.max_workers,
            wait_for_completion=not args.no_wait,
            auto_download=args.download
            # Note: For CSV files, titles come from the CSV. For text files, filenames are used as titles.
            # The --title argument only applies to single file processing.
        )
        
        # Print summary
        print(f"\nBatch Processing Results:")
        print(f"  Total files: {result['total_files']}")
        print(f"  Successful submissions: {result['successful_submissions']}")
        print(f"  Failed submissions: {result['failed_submissions']}")
        
        if not args.no_wait:
            print(f"  Completed jobs: {result['completed_jobs']}")
            print(f"  Failed jobs: {result['failed_jobs']}")
        
        if result['errors']:
            print(f"\nErrors ({len(result['errors'])}):")
            for error in result['errors']:
                print(f"  {error['file_path']}: {error['error']}")
        
        # Optionally print detailed results
        if args.log_level == 'DEBUG':
            print(f"\nDetailed Results: {json.dumps(result, indent=2, default=str)}")
        
        # Exit with error code if there were failures
        if result['failed_submissions'] > 0 or (not args.no_wait and result['failed_jobs'] > 0):
            exit(1)


if __name__ == '__main__':
    main()
