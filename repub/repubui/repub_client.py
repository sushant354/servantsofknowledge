#!/usr/bin/env python3
"""
REPUB Python Client

A Python client for submitting document processing jobs to REPUB UI
using Django REST Framework token authentication.
"""

import requests
import json
import os
import time
import logging
from typing import Optional, Dict, Any, Union
from pathlib import Path


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


def main():
    """Example usage of the REPUB client"""
    import argparse
    
    parser = argparse.ArgumentParser(description='REPUB Python Client')
    parser.add_argument('--url', default='http://localhost:8000', 
                       help='REPUB server URL (default: http://localhost:8000)')
    parser.add_argument('--token', required=True, 
                       help='REST framework authentication token')
    parser.add_argument('--file', required=True, 
                       help='Path to file to process')
    parser.add_argument('--output', 
                       help='Output file path (optional)')
    parser.add_argument('--title', 
                       help='Job title (optional)')
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
    
    # Process document
    result = client.process_document(
        file_path=args.file,
        output_path=args.output,
        title=args.title,
        wait_for_completion=not args.no_wait,
        auto_download=args.download
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")


if __name__ == '__main__':
    main()
