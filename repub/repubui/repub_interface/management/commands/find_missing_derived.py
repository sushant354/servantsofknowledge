import os
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.conf import settings
from repub_interface.models import ProcessingJob


class Command(BaseCommand):
    help = 'Find jobs where is_derived is True but the derived directory does not exist, and jobs with duplicate derived_identifiers'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Reset is_derived to False for jobs with missing derived directories',
        )

    def handle(self, *args, **options):
        # Get all jobs where is_derived is True
        derived_jobs = ProcessingJob.objects.filter(is_derived=True)

        self.stdout.write(f"Checking {derived_jobs.count()} derived jobs...")

        missing_derived = []
        identifier_to_jobs = defaultdict(list)

        for job in derived_jobs:
            derived_dir = job.get_derived_dir()

            if derived_dir is None:
                self.stdout.write(
                    self.style.WARNING(f"Job {job.id}: is_derived=True but no derived_identifier set")
                )
                missing_derived.append(job)
            elif not os.path.exists(derived_dir):
                self.stdout.write(
                    self.style.WARNING(f"Job {job.id}: derived_dir does not exist: {derived_dir}")
                )
                missing_derived.append(job)

            if job.derived_identifier:
                identifier_to_jobs[job.derived_identifier].append(job)

        # Find duplicates
        duplicate_identifiers = {
            identifier: jobs
            for identifier, jobs in identifier_to_jobs.items()
            if len(jobs) > 1
        }

        # Summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS(f"Summary:"))
        self.stdout.write(f"  Total derived jobs: {derived_jobs.count()}")
        self.stdout.write(f"  Missing derived directories: {len(missing_derived)}")
        self.stdout.write(f"  Duplicate derived_identifiers: {len(duplicate_identifiers)}")
        self.stdout.write("=" * 50)

        if missing_derived:
            self.stdout.write("\nJobs with missing derived directories:")
            for job in missing_derived:
                self.stdout.write(f"  - {job.id} (identifier: {job.identifier}, derived_identifier: {job.derived_identifier})")

            if options['reset']:
                self.stdout.write("\nResetting is_derived to False for these jobs...")
                for job in missing_derived:
                    job.is_derived = False
                    job.derived_identifier = None
                    job.derived_at = None
                    job.save()
                    self.stdout.write(self.style.SUCCESS(f"  Reset job {job.id}"))
                self.stdout.write(self.style.SUCCESS(f"\nReset {len(missing_derived)} jobs"))

        if duplicate_identifiers:
            self.stdout.write(self.style.WARNING(f"\nDuplicate derived_identifiers:"))
            for identifier, jobs in duplicate_identifiers.items():
                self.stdout.write(self.style.WARNING(f"\n  derived_identifier: {identifier} ({len(jobs)} jobs)"))
                for job in jobs:
                    self.stdout.write(f"    - {job.id} (identifier: {job.identifier}, status: {job.status}, derived_at: {job.derived_at})")
