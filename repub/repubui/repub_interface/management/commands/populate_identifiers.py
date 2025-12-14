import os
import zipfile
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from repub_interface.models import ProcessingJob
from repub.utils.scandir import Scandir
from repub.utils import pdfs


class Command(BaseCommand):
    help = 'Populate identifier and author fields for existing jobs that are missing them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Update all jobs, even if they already have identifier and author fields',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        logger = logging.getLogger(__name__)
        
        # Get jobs to update
        if options['all']:
            jobs = ProcessingJob.objects.all()
            self.stdout.write(f"Processing all {jobs.count()} jobs...")
        else:
            jobs = ProcessingJob.objects.filter(
                identifier__isnull=True
            ) | ProcessingJob.objects.filter(
                identifier=''
            ) | ProcessingJob.objects.filter(
                author__isnull=True
            ) | ProcessingJob.objects.filter(
                author=''
            )
            self.stdout.write(f"Processing {jobs.count()} jobs without identifiers or authors...")

        updated_count = 0
        error_count = 0
        skipped_count = 0

        for job in jobs:
            try:
                # Skip if no input file
                if not job.input_file:
                    self.stdout.write(self.style.WARNING(f"Job {job.id}: No input file, skipping"))
                    skipped_count += 1
                    continue

                input_file_path = job.input_file.path
                if not os.path.exists(input_file_path):
                    self.stdout.write(self.style.WARNING(f"Job {job.id}: Input file not found at {input_file_path}, skipping"))
                    skipped_count += 1
                    continue

                input_dir = job.get_input_dir()

                # Get metadata based on input type
                metadata = None
                if job.input_type == 'pdf':
                    try:
                        metadata = pdfs.get_metadata(input_file_path)
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Job {job.id}: Error reading PDF metadata: {e}"))
                        error_count += 1
                        continue
                else:
                    # For images/zip
                    if not os.path.exists(input_dir):
                        # Try to extract if it's a zip
                        if input_file_path.lower().endswith('.zip'):
                            try:
                                os.makedirs(input_dir, exist_ok=True)
                                with zipfile.ZipFile(input_file_path, 'r') as zip_ref:
                                    zip_ref.extractall(input_dir)
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f"Job {job.id}: Error extracting zip: {e}"))
                                error_count += 1
                                continue
                    
                    try:
                        scandir = Scandir(input_dir, None, None, logger)
                        metadata = scandir.metadata
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Job {job.id}: Error reading image metadata: {e}"))
                        error_count += 1
                        continue

                if metadata:
                    identifier = metadata.get('/Identifier')
                    author = metadata.get('/Creator')

                    if identifier or author:
                        updates = []
                        if identifier:
                            updates.append(f"identifier to '{identifier}'")
                        if author:
                            updates.append(f"author to '{author}'")

                        update_msg = ", ".join(updates)

                        if options['dry_run']:
                            self.stdout.write(self.style.SUCCESS(f"Job {job.id}: Would set {update_msg}"))
                        else:
                            if identifier:
                                job.identifier = identifier
                            if author:
                                job.author = author
                            job.save()
                            self.stdout.write(self.style.SUCCESS(f"Job {job.id}: Set {update_msg}"))
                        updated_count += 1
                    else:
                        self.stdout.write(self.style.WARNING(f"Job {job.id}: No identifier or author found in metadata"))
                        skipped_count += 1
                else:
                    self.stdout.write(self.style.WARNING(f"Job {job.id}: Could not extract metadata"))
                    skipped_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Job {job.id}: Unexpected error: {e}"))
                error_count += 1

        # Summary
        self.stdout.write("\n" + "="*50)
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(f"DRY RUN - No changes made"))
        self.stdout.write(self.style.SUCCESS(f"Summary:"))
        self.stdout.write(f"  Updated: {updated_count}")
        self.stdout.write(f"  Skipped: {skipped_count}")
        self.stdout.write(f"  Errors: {error_count}")
        self.stdout.write("="*50)
