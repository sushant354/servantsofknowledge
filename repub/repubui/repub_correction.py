#!/usr/bin/env python3
"""
REPUB Correction Client

A Python client for submitting correction zip files to REPUB UI
for jobs that are in 'under_correction' status.

Usage:
    python repub_correction.py --token <token> --job-id <job-id> --zip corrections.zip
    python repub_correction.py --token <token> --job-id <job-id> --zip corrections.zip --url http://repub.example.com
    python repub_correction.py --token <token> --job-id <job-id> --zip corrections.zip --wait --download --output result.pdf
"""

import requests
import json
import time
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, Union


class REPUBCorrectionClient:
    """Python client for submitting corrections to the REPUB service"""

    def __init__(self, base_url: str, token: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the correction client

        Args:
            base_url: Base URL of the REPUB service (e.g., 'http://localhost:8000')
            token: REST framework authentication token
            logger: Optional logger instance
        """
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.logger = logger or logging.getLogger(__name__)
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Token {token}',
            'User-Agent': 'REPUB-Correction-Client/1.0'
        })

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

    def submit_correction(self, job_id: str, zip_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Submit a corrections zip file for a job under correction.

        Args:
            job_id: UUID of the job
            zip_path: Path to the corrections zip file

        Returns:
            Dict containing the API response
        """
        zip_path = Path(zip_path)

        if not zip_path.exists():
            raise FileNotFoundError(f"File not found: {zip_path}")

        if not zip_path.name.endswith('.zip'):
            raise ValueError(f"File must be a .zip file: {zip_path}")

        self.logger.info(f"Submitting correction zip '{zip_path}' for job {job_id}")

        with open(zip_path, 'rb') as f:
            files = {'correction_zip': (zip_path.name, f, 'application/zip')}

            try:
                response = self.session.post(
                    f"{self.base_url}/api/job/{job_id}/submit-correction-zip/",
                    files=files
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                try:
                    error_data = e.response.json()
                except (ValueError, AttributeError):
                    error_data = {'error': e.response.text[:500]}
                return {
                    'success': False,
                    'status_code': e.response.status_code,
                    'message': f'Request failed: {e}',
                    **error_data
                }
            except Exception as e:
                return {
                    'success': False,
                    'message': f'Request failed: {str(e)}'
                }

    def wait_for_completion(self, job_id: str,
                            timeout: int = 3600,
                            poll_interval: int = 10) -> Dict[str, Any]:
        """
        Wait for a job to complete after correction resubmission.

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

            if not status.get('success', True):
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


def main():
    parser = argparse.ArgumentParser(description='REPUB Correction Client - Submit corrected scans')
    parser.add_argument('--url', default='http://localhost:8000',
                        help='REPUB server URL (default: http://localhost:8000)')
    parser.add_argument('--token', required=True,
                        help='REST framework authentication token')
    parser.add_argument('--job-id', required=True,
                        help='UUID of the job under correction')
    parser.add_argument('--zip', required=True, dest='zip_path',
                        help='Path to the corrections zip file')
    parser.add_argument('--wait', action='store_true',
                        help='Wait for reprocessing to complete')
    parser.add_argument('--download', action='store_true',
                        help='Download result after reprocessing completes (implies --wait)')
    parser.add_argument('--output',
                        help='Output file path for downloaded result')
    parser.add_argument('--timeout', type=int, default=3600,
                        help='Timeout in seconds when waiting for completion (default: 3600)')
    parser.add_argument('--poll-interval', type=int, default=10,
                        help='Poll interval in seconds (default: 10)')
    parser.add_argument('--logfile',
                        help='Log file path (optional, logs to console if not specified)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        default='INFO', help='Logging level (default: INFO)')

    args = parser.parse_args()

    # Setup logging
    logger = logging.getLogger('repub_correction')
    logger.setLevel(getattr(logging, args.log_level))

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    if args.logfile:
        file_handler = logging.FileHandler(args.logfile)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    else:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    client = REPUBCorrectionClient(args.url, args.token, logger=logger)

    # Check job status first
    status = client.get_job_status(args.job_id)
    if not status.get('success', True):
        print(f"Error checking job status: {status.get('message', 'Unknown error')}")
        exit(1)

    job_status = status.get('status')
    if job_status != 'under_correction':
        print(f"Job {args.job_id} is not under correction. Current status: {job_status}")
        exit(1)

    # Submit corrections
    result = client.submit_correction(args.job_id, args.zip_path)
    print(f"Submission result: {json.dumps(result, indent=2)}")

    if not result.get('success'):
        exit(1)

    # Wait for completion if requested
    wait = args.wait or args.download
    if wait:
        logger.info("Waiting for reprocessing to complete...")
        final_status = client.wait_for_completion(
            args.job_id,
            timeout=args.timeout,
            poll_interval=args.poll_interval
        )

        print(f"Final status: {json.dumps(final_status, indent=2)}")

        if final_status.get('status') == 'completed' and args.download:
            if client.download_result(args.job_id, args.output):
                print(f"Result downloaded to: {args.output or 'current directory'}")
            else:
                print("Failed to download result")
                exit(1)
        elif final_status.get('status') != 'completed':
            exit(1)


if __name__ == '__main__':
    main()
