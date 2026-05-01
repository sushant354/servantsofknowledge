#!/usr/bin/env python3
"""
REPUB Download Corrections Client

Downloads corrections folders from REPUB UI for jobs under correction.
Can filter by identifier, job ID, or date range. Downloads all if no
filter is specified.

Saves each corrections folder using the job identifier as the directory
name, falling back to job ID if no identifier is set.

Usage:
    # Download all corrections
    python repub_download_corrections.py --token <token>

    # Download by identifier
    python repub_download_corrections.py --token <token> --identifier <identifier>

    # Download by job ID
    python repub_download_corrections.py --token <token> --job-id <job-id>

    # Download by date range
    python repub_download_corrections.py --token <token> --from-date 2026-03-01 --to-date 2026-03-28

    # Specify output directory
    python repub_download_corrections.py --token <token> --output-dir ./corrections
"""

import requests
import json
import os
import logging
import argparse
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List


class REPUBDownloadCorrectionsClient:
    """Client for downloading corrections folders from the REPUB service"""

    def __init__(self, base_url: str, token: str, logger: Optional[logging.Logger] = None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.logger = logger or logging.getLogger(__name__)
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Token {token}',
            'User-Agent': 'REPUB-Download-Corrections-Client/1.0'
        })

    def list_corrections(self,
                         job_id: Optional[str] = None,
                         identifier: Optional[str] = None,
                         from_date: Optional[str] = None,
                         to_date: Optional[str] = None) -> Dict[str, Any]:
        """
        List jobs under correction, optionally filtered.

        Args:
            job_id: Filter by job UUID
            identifier: Filter by job identifier
            from_date: Filter by created_at >= date (YYYY-MM-DD)
            to_date: Filter by created_at <= date (YYYY-MM-DD)

        Returns:
            API response with list of jobs
        """
        params = {}
        if job_id:
            params['job_id'] = job_id
        if identifier:
            params['identifier'] = identifier
        if from_date:
            params['from_date'] = from_date
        if to_date:
            params['to_date'] = to_date

        try:
            response = self.session.get(
                f"{self.base_url}/api/corrections/",
                params=params
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
                'message': f'Request failed: {e}',
                **error_data
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Request failed: {str(e)}'
            }

    def download_corrections_by_job_id(self, job_id: str) -> Optional[bytes]:
        """Download corrections zip for a job by job ID. Returns zip bytes or None."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/job/{job_id}/download-corrections/"
            )
            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(f"Failed to download corrections for job {job_id}: {e}")
            return None

    def download_corrections_by_identifier(self, identifier: str) -> Optional[bytes]:
        """Download corrections zip for a job by identifier. Returns zip bytes or None."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/job/identifier/{identifier}/download-corrections/"
            )
            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(f"Failed to download corrections for identifier '{identifier}': {e}")
            return None

    def download_and_extract(self, job_info: Dict[str, Any], output_dir: Path) -> bool:
        """
        Download corrections for a job and extract to a folder.

        The folder is named by identifier if available, otherwise by job_id.

        Args:
            job_info: Dict with job_id, identifier, etc. from list_corrections
            output_dir: Parent directory to save the corrections folder into

        Returns:
            True if successful
        """
        job_id = job_info['job_id']
        identifier = job_info.get('identifier', '').strip()

        # Determine folder name: identifier if available, job_id otherwise
        if identifier:
            folder_name = identifier.replace('/', '_').replace('\\', '_')
        else:
            folder_name = job_id

        dest_dir = output_dir / folder_name

        # Download the zip
        self.logger.info(f"Downloading corrections for {folder_name} (job {job_id})...")

        if identifier:
            zip_bytes = self.download_corrections_by_identifier(identifier)
        else:
            zip_bytes = self.download_corrections_by_job_id(job_id)

        if zip_bytes is None:
            return False

        # Extract to destination
        dest_dir.mkdir(parents=True, exist_ok=True)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.zip')
        try:
            with os.fdopen(tmp_fd, 'wb') as f:
                f.write(zip_bytes)

            with zipfile.ZipFile(tmp_path, 'r') as zf:
                zf.extractall(dest_dir)

            self.logger.info(f"Extracted corrections to: {dest_dir}")
            return True
        except zipfile.BadZipFile:
            self.logger.error(f"Downloaded file for {folder_name} is not a valid zip")
            return False
        finally:
            os.unlink(tmp_path)


def main():
    parser = argparse.ArgumentParser(
        description='REPUB Download Corrections - Download corrections folders from the server'
    )
    parser.add_argument('--url', default='http://localhost:8000',
                        help='REPUB server URL (default: http://localhost:8000)')
    parser.add_argument('--token', required=True,
                        help='REST framework authentication token')
    parser.add_argument('--job-id',
                        help='Download corrections for a specific job UUID')
    parser.add_argument('--identifier',
                        help='Download corrections for a specific job identifier')
    parser.add_argument('--from-date',
                        help='Filter jobs created on or after this date (YYYY-MM-DD)')
    parser.add_argument('--to-date',
                        help='Filter jobs created on or before this date (YYYY-MM-DD)')
    parser.add_argument('--output-dir', default='./corrections',
                        help='Directory to save corrections folders (default: ./corrections)')
    parser.add_argument('--list-only', action='store_true',
                        help='Only list jobs under correction, do not download')
    parser.add_argument('--logfile',
                        help='Log file path (optional)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        default='INFO', help='Logging level (default: INFO)')

    args = parser.parse_args()

    # Setup logging
    logger = logging.getLogger('repub_download_corrections')
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

    client = REPUBDownloadCorrectionsClient(args.url, args.token, logger=logger)

    # List jobs under correction
    result = client.list_corrections(
        job_id=args.job_id,
        identifier=args.identifier,
        from_date=args.from_date,
        to_date=args.to_date
    )

    if not result.get('success'):
        print(f"Error: {result.get('message', result.get('error', 'Unknown error'))}")
        exit(1)

    jobs = result.get('jobs', [])
    count = result.get('count', 0)

    if count == 0:
        print("No jobs under correction found matching the criteria.")
        exit(0)

    # Print job list
    print(f"\nFound {count} job(s) under correction:\n")
    print(f"{'Identifier':<40} {'Job ID':<38} {'Title':<30} {'Created':<20} {'Has Folder'}")
    print("-" * 140)
    for job in jobs:
        identifier = job.get('identifier', '') or '-'
        print(f"{identifier:<40} {job['job_id']:<38} {(job.get('title', '') or '-'):<30} "
              f"{job['created_at'][:19]:<20} {'Yes' if job['has_corrections_folder'] else 'No'}")

    if args.list_only:
        exit(0)

    # Filter to only jobs with corrections folders
    downloadable = [j for j in jobs if j['has_corrections_folder']]
    if not downloadable:
        print("\nNo jobs have corrections folders to download.")
        exit(0)

    # Download
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading {len(downloadable)} corrections folder(s) to: {output_dir}\n")

    success_count = 0
    fail_count = 0

    for job in downloadable:
        folder_name = job.get('identifier', '').strip() or job['job_id']
        if client.download_and_extract(job, output_dir):
            success_count += 1
            print(f"  OK: {folder_name}")
        else:
            fail_count += 1
            print(f"  FAILED: {folder_name}")

    print(f"\nDone. Downloaded: {success_count}, Failed: {fail_count}")
    if fail_count > 0:
        exit(1)


if __name__ == '__main__':
    main()
