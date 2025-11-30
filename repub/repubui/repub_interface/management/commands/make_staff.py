from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Make a user staff and/or superuser'

    def add_arguments(self, parser):
        parser.add_argument(
            'username',
            type=str,
            help='Username of the user to modify'
        )
        parser.add_argument(
            '--staff',
            action='store_true',
            help='Make the user a staff member'
        )
        parser.add_argument(
            '--superuser',
            action='store_true',
            help='Make the user a superuser (also makes them staff)'
        )
        parser.add_argument(
            '--remove',
            action='store_true',
            help='Remove staff/superuser privileges instead of adding them'
        )

    def handle(self, *args, **options):
        username = options['username']
        make_staff = options['staff']
        make_superuser = options['superuser']
        remove = options['remove']

        # If neither flag is specified, default to making staff
        if not make_staff and not make_superuser:
            make_staff = True

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'User "{username}" does not exist')

        # Store original state for reporting
        was_staff = user.is_staff
        was_superuser = user.is_superuser

        if remove:
            # Remove privileges
            if make_superuser:
                user.is_superuser = False
                user.is_staff = False  # Superusers must be staff, so remove both
            elif make_staff:
                user.is_staff = False

            user.save()

            # Report what was done
            changes = []
            if was_superuser and not user.is_superuser:
                changes.append('superuser')
            if was_staff and not user.is_staff:
                changes.append('staff')

            if changes:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Successfully removed {" and ".join(changes)} privileges from user "{username}"'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'User "{username}" did not have the specified privileges'
                    )
                )
        else:
            # Add privileges
            if make_superuser:
                user.is_superuser = True
                user.is_staff = True  # Superusers must be staff
            elif make_staff:
                user.is_staff = True

            user.save()

            # Report what was done
            changes = []
            if not was_superuser and user.is_superuser:
                changes.append('superuser')
            if not was_staff and user.is_staff:
                changes.append('staff')

            if changes:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Successfully made user "{username}" {" and ".join(changes)}'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'User "{username}" already has the specified privileges'
                    )
                )

        # Display current status
        status = []
        if user.is_superuser:
            status.append('superuser')
        if user.is_staff:
            status.append('staff')
        if not status:
            status.append('regular user')

        self.stdout.write(
            self.style.NOTICE(
                f'\nCurrent status for "{username}": {", ".join(status)}'
            )
        )
