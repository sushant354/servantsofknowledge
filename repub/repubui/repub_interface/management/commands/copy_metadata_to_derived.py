import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from repub_interface.models import ProcessingJob
from repub.utils.scandir import Scandir


class Command(BaseCommand):
    help = 'Copy metadata.xml and scandata.json from input zip to derived directory for already derived jobs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--job-id',
            type=str,
            help='Process a specific job by UUID',
        )
        parser.add_argument(
            '--derived-after',
            type=str,
            help='Only process jobs derived after this date/time (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)',
        )
        parser.add_argument(
            '--derived-before',
            type=str,
            help='Only process jobs derived before this date/time (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def _parse_datetime(self, value):
        """Parse a date or datetime string into a timezone-aware datetime."""
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(value, fmt)
                return timezone.make_aware(dt)
            except ValueError:
                continue
        raise ValueError(f"Invalid date format: '{value}'. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")

    def handle(self, *args, **options):
        if options['job_id']:
            jobs = ProcessingJob.objects.filter(id=options['job_id'], is_derived=True)
            if not jobs.exists():
                self.stdout.write(self.style.ERROR(
                    f"Job {options['job_id']} not found or is not derived"
                ))
                return
        else:
            jobs = ProcessingJob.objects.filter(is_derived=True)

        if options['derived_after']:
            derived_after = self._parse_datetime(options['derived_after'])
            jobs = jobs.filter(derived_at__gte=derived_after)
            self.stdout.write(f"Filtering: derived_at >= {derived_after}")

        if options['derived_before']:
            derived_before = self._parse_datetime(options['derived_before'])
            jobs = jobs.filter(derived_at__lte=derived_before)
            self.stdout.write(f"Filtering: derived_at <= {derived_before}")

        self.stdout.write(f"Processing {jobs.count()} derived jobs...")

        updated_count = 0
        skipped_count = 0
        error_count = 0

        for job in jobs:
            try:
                derive_dir = job.get_derived_dir()
                if not derive_dir or not os.path.exists(derive_dir):
                    self.stdout.write(self.style.WARNING(
                        f"Job {job.id}: Derived directory not found, skipping"
                    ))
                    skipped_count += 1
                    continue

                # Check if both files already exist in derived dir
                metadata_dest = os.path.join(derive_dir, 'metadata.xml')
                scandata_dest = os.path.join(derive_dir, 'scandata.json')
                if os.path.exists(metadata_dest) and os.path.exists(scandata_dest):
                    self.stdout.write(self.style.WARNING(
                        f"Job {job.id}: metadata.xml and scandata.json already exist, skipping"
                    ))
                    skipped_count += 1
                    continue

                # Find the input zip in the derived directory
                input_zip = None
                for filename in os.listdir(derive_dir):
                    if filename.endswith('.zip') and not filename.endswith(('_images.zip', '_thumbnails.zip')):
                        input_zip = os.path.join(derive_dir, filename)
                        break

                if not input_zip:
                    self.stdout.write(self.style.WARNING(
                        f"Job {job.id}: No input zip found in derived directory, skipping"
                    ))
                    skipped_count += 1
                    continue

                if options['dry_run']:
                    self.stdout.write(self.style.SUCCESS(
                        f"Job {job.id}: Would extract metadata.xml and scandata.json from {os.path.basename(input_zip)}"
                    ))
                    updated_count += 1
                    continue

                # Extract to temp directory
                temp_dir = tempfile.mkdtemp()
                try:
                    with zipfile.ZipFile(input_zip, 'r') as zip_ref:
                        zip_ref.extractall(temp_dir)

                    # Use Scandir to resolve the actual input directory
                    scandir = Scandir(temp_dir, None, None)
                    resolved_dir = scandir.indir

                    copied = []
                    for target_file, dest_path in [('metadata.xml', metadata_dest), ('scandata.json', scandata_dest)]:
                        if os.path.exists(dest_path):
                            copied.append(f"{target_file} (already exists)")
                            continue
                        source_path = os.path.join(resolved_dir, target_file)
                        if os.path.exists(source_path):
                            shutil.copy2(source_path, dest_path)
                            copied.append(target_file)
                        else:
                            copied.append(f"{target_file} (not found in zip)")

                    self.stdout.write(self.style.SUCCESS(
                        f"Job {job.id}: {', '.join(copied)}"
                    ))
                    updated_count += 1
                finally:
                    shutil.rmtree(temp_dir)

            except zipfile.BadZipFile:
                self.stdout.write(self.style.ERROR(
                    f"Job {job.id}: Input file is not a valid zip"
                ))
                error_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"Job {job.id}: Unexpected error: {e}"
                ))
                error_count += 1

        # Summary
        self.stdout.write("\n" + "=" * 50)
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS("DRY RUN - No changes made"))
        self.stdout.write(self.style.SUCCESS("Summary:"))
        self.stdout.write(f"  Updated: {updated_count}")
        self.stdout.write(f"  Skipped: {skipped_count}")
        self.stdout.write(f"  Errors: {error_count}")
        self.stdout.write("=" * 50)

