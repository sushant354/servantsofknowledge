import os
import logging
import shutil
import datetime 
import csv
import mimetypes

from django.shortcuts import render, redirect
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import HttpResponse

from .models import ProcessingJob
from .utils  import format_file_size

logger = logging.getLogger('repubui.items')


@login_required
def all_items(request):
    """View all derived items"""
    derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')

    # Get search parameters from request
    search_identifier = request.GET.get('identifier', '').strip()
    search_identifier_prefix = request.GET.get('identifier_prefix', '').strip()
    search_author = request.GET.get('author', '').strip()

    # Get sort parameters (default: derived_at descending)
    sort_by = request.GET.get('sort', 'derived_at')
    sort_order = request.GET.get('order', 'desc')

    # Get derived jobs - staff users see all, regular users see only their own
    if request.user.is_staff:
        derived_jobs = ProcessingJob.objects.filter(is_derived=True)
    else:
        derived_jobs = ProcessingJob.objects.filter(is_derived=True, user=request.user)

    # Create a mapping of identifier to job info (owner, author, and derived_at)
    identifier_to_job_info = {}
    for job in derived_jobs:
        if job.derived_identifier:
            identifier_to_job_info[job.derived_identifier] = {
                'owner': job.user,
                'author': job.author,
                'derived_at': job.derived_at,
            }

    # Check if derived directory exists
    if not os.path.exists(derive_base_dir):
        items = []
    else:
        items = []
        for identifier in sorted(os.listdir(derive_base_dir)):
            item_path = os.path.join(derive_base_dir, identifier)

            # Only include directories
            if os.path.isdir(item_path):
                # For non-staff users, only show items they own
                if not request.user.is_staff and identifier not in identifier_to_job_info:
                    continue

                job_info = identifier_to_job_info.get(identifier, {})
                item_info = {
                    'identifier': identifier,
                    'path': item_path,
                    'owner': job_info.get('owner'),  # Add owner information
                    'author': job_info.get('author'),  # Add author information
                    'derived_at': job_info.get('derived_at'),  # Add derived timestamp
                }

                # Get statistics about the directory
                try:
                    file_count = 0
                    total_size = 0
                    modified_time = None

                    for root, dirs, files in os.walk(item_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            file_count += 1
                            total_size += os.path.getsize(file_path)

                    # Get the most recent modification time
                    stat_info = os.stat(item_path)
                    modified_time = timezone.datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.get_current_timezone())

                    item_info['file_count'] = file_count
                    item_info['total_size'] = format_file_size(total_size)
                    item_info['total_size_bytes'] = total_size
                    item_info['modified'] = modified_time

                    # Check for thumbnail
                    thumb_path = os.path.join(item_path, '__ia_thumb.jpg')
                    if os.path.exists(thumb_path):
                        relative_path = os.path.relpath(thumb_path, settings.MEDIA_ROOT)
                        item_info['thumbnail_url'] = f"{settings.MEDIA_URL}{relative_path}"
                    else:
                        item_info['thumbnail_url'] = None

                except Exception as e:
                    logger.error(f"Error reading item {identifier}: {str(e)}")
                    item_info['file_count'] = 0
                    item_info['total_size'] = "Unknown"
                    item_info['modified'] = None
                    item_info['thumbnail_url'] = None

                items.append(item_info)

    # Filter items based on search criteria
    if search_identifier or search_identifier_prefix or search_author:
        filtered_items = []
        for item in items:
            # Check identifier exact match
            if search_identifier:
                if item['identifier'].lower() != search_identifier.lower():
                    continue

            # Check identifier prefix match
            if search_identifier_prefix:
                if not item['identifier'].lower().startswith(search_identifier_prefix.lower()):
                    continue

            # Check author match (case-insensitive contains)
            if search_author:
                author = item.get('author') or ''
                if search_author.lower() not in author.lower():
                    continue

            filtered_items.append(item)
        items = filtered_items

    has_search = bool(search_identifier or search_identifier_prefix or search_author)

    # Sort items based on sort parameters
    reverse_order = (sort_order == 'desc')
    if sort_by == 'identifier':
        items.sort(key=lambda x: (x.get('identifier') or '').lower(), reverse=reverse_order)
    elif sort_by == 'author':
        items.sort(key=lambda x: (x.get('author') or '').lower(), reverse=reverse_order)
    elif sort_by == 'owner':
        items.sort(key=lambda x: (x.get('owner').username if x.get('owner') else '').lower(), reverse=reverse_order)
    elif sort_by == 'size':
        items.sort(key=lambda x: x.get('total_size_bytes', 0), reverse=reverse_order)
    elif sort_by == 'files':
        items.sort(key=lambda x: x.get('file_count', 0), reverse=reverse_order)
    else:  # Default: derived_at
        sort_by = 'derived_at'
        items.sort(key=lambda x: x.get('derived_at') or timezone.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=reverse_order)

    context = {
        'items': items,
        'total_items': len(items),
        'search_identifier': search_identifier,
        'search_identifier_prefix': search_identifier_prefix,
        'search_author': search_author,
        'has_search': has_search,
        'sort_by': sort_by,
        'sort_order': sort_order,
    }

    return render(request, 'repub_interface/all_items.html', context)


@login_required
@require_http_methods(["POST"])
def export_items_csv(request):
    """Export selected items as CSV"""
    identifiers_str = request.POST.get('identifiers', '')
    if not identifiers_str:
        messages.error(request, 'No items selected for export.')
        return redirect('all_items')

    identifiers = [i.strip() for i in identifiers_str.split(',') if i.strip()]
    if not identifiers:
        messages.error(request, 'No items selected for export.')
        return redirect('all_items')

    # Get derived jobs for selected identifiers
    if request.user.is_staff:
        derived_jobs = ProcessingJob.objects.filter(
            is_derived=True,
            derived_identifier__in=identifiers
        )
    else:
        derived_jobs = ProcessingJob.objects.filter(
            is_derived=True,
            derived_identifier__in=identifiers,
            user=request.user
        )

    # Create a mapping of identifier to job info
    identifier_to_job = {}
    for job in derived_jobs:
        if job.derived_identifier:
            identifier_to_job[job.derived_identifier] = job

    # Build CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="items_export.csv"'

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)
    # Write header
    writer.writerow(['Identifier', 'Author', 'Owner', 'Files', 'Size', 'Last Modified'])

    derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')

    for identifier in identifiers:
        item_path = os.path.join(derive_base_dir, identifier)
        if not os.path.isdir(item_path):
            continue

        # Check access for non-staff users
        if not request.user.is_staff and identifier not in identifier_to_job:
            continue

        job = identifier_to_job.get(identifier)
        author = job.author if job else ''
        owner = job.user.username if job and job.user else ''

        # Get directory stats
        file_count = 0
        total_size = 0
        modified_time = ''

        try:
            for root, dirs, files in os.walk(item_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_count += 1
                    total_size += os.path.getsize(file_path)

            stat_info = os.stat(item_path)
            modified_time = timezone.datetime.fromtimestamp(
                stat_info.st_mtime,
                tz=timezone.get_current_timezone()
            ).strftime('%Y-%m-%d %H:%M:%S')
        except OSError:
            pass

        writer.writerow([
            identifier,
            author,
            owner,
            file_count,
            format_file_size(total_size),
            modified_time
        ])

    return response


@login_required
def item_directory(request, identifier, subpath=''):
    """View derived directory contents by identifier"""
    # Check ownership - regular users can only access their own items
    if not request.user.is_staff:
        derived_job = ProcessingJob.objects.filter(
            is_derived=True,
            derived_identifier=identifier,
            user=request.user
        ).first()

        if not derived_job:
            messages.error(request, 'Access denied. You do not have permission to view this item.')
            return redirect('all_items')

    # Get the derive directory path
    derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')
    item_dir = os.path.join(derive_base_dir, identifier)

    # Check if the directory exists
    if not os.path.exists(item_dir):
        messages.error(request, f'Derived directory for identifier "{identifier}" does not exist.')
        return redirect('all_items')

    # Construct the current directory path
    if subpath:
        # Sanitize the subpath to prevent directory traversal
        subpath = subpath.strip('/')
        subpath_parts = [part for part in subpath.split('/') if part and part != '..']
        current_dir = os.path.join(item_dir, *subpath_parts)
        current_subpath = '/'.join(subpath_parts)
    else:
        current_dir = item_dir
        current_subpath = ''

    # Security check: ensure we're still within the item directory
    if not os.path.commonpath([item_dir, current_dir]) == item_dir:
        messages.error(request, 'Access denied: Invalid directory path.')
        return redirect('item_directory', identifier=identifier)

    if not os.path.exists(current_dir):
        messages.error(request, 'Directory does not exist.')
        return redirect('item_directory', identifier=identifier)

    # Build breadcrumb navigation
    breadcrumbs = [{'name': identifier, 'path': ''}]
    if current_subpath:
        path_parts = current_subpath.split('/')
        for i, part in enumerate(path_parts):
            breadcrumb_path = '/'.join(path_parts[:i+1])
            breadcrumbs.append({'name': part, 'path': breadcrumb_path})

    # Get directory contents
    items = []
    for item_name in sorted(os.listdir(current_dir)):
        item_path = os.path.join(current_dir, item_name)
        is_dir = os.path.isdir(item_path)

        item_info = {
            'name': item_name,
            'is_directory': is_dir,
            'size': None,
            'modified': None,
            'mime_type': None,
            'relative_url': None,
            'subpath': os.path.join(current_subpath, item_name).replace('\\', '/') if current_subpath else item_name
        }

        if is_dir:
            # Count items in subdirectory
            try:
                subitem_count = len(os.listdir(item_path))
                item_info['size'] = f"{subitem_count} items"
            except:
                item_info['size'] = "Unknown"
        else:
            # Get file info
            stat_info = os.stat(item_path)
            item_info['size'] = format_file_size(stat_info.st_size)
            item_info['modified'] = timezone.datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.get_current_timezone())

            # Get mime type
            mime_type, _ = mimetypes.guess_type(item_path)
            item_info['mime_type'] = mime_type

            # Create relative URL for media files
            relative_path = os.path.relpath(item_path, settings.MEDIA_ROOT)
            item_info['relative_url'] = f"{settings.MEDIA_URL}{relative_path}"

        items.append(item_info)

    # Separate directories and files
    directories = [item for item in items if item['is_directory']]
    files = [item for item in items if not item['is_directory']]

    context = {
        'identifier': identifier,
        'current_dir': current_dir,
        'current_subpath': current_subpath,
        'breadcrumbs': breadcrumbs,
        'directories': directories,
        'files': files,
        'total_items': len(items),
        'parent_path': '/'.join(current_subpath.split('/')[:-1]) if current_subpath and '/' in current_subpath else '' if current_subpath else None,
        'is_item_directory': True
    }

    return render(request, 'repub_interface/item_directory.html', context)


@login_required
@require_http_methods(["POST"])
def delete_item(request, identifier):
    """Delete a derived item directory (staff only)"""
    # Only allow staff users to delete items
    if not request.user.is_staff:
        messages.error(request, 'Permission denied. Only staff members can delete items.')
        return redirect('item_directory', identifier=identifier)

    # Get the derive directory path
    derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')
    item_dir = os.path.join(derive_base_dir, identifier)

    # Check if the directory exists
    if not os.path.exists(item_dir):
        messages.error(request, f'Derived directory for identifier "{identifier}" does not exist.')
        return redirect('all_items')

    # Security check: ensure we're within the derived directory
    if not os.path.commonpath([derive_base_dir, item_dir]) == derive_base_dir:
        messages.error(request, 'Access denied: Invalid directory path.')
        return redirect('all_items')

    try:
        # Delete the entire directory
        shutil.rmtree(item_dir)
        logger.info(f"Staff user {request.user.username} deleted item: {identifier}")
        messages.success(request, f'Item "{identifier}" has been deleted successfully.')
    except Exception as e:
        logger.error(f"Error deleting item {identifier}: {str(e)}", exc_info=True)
        messages.error(request, f'Error deleting item: {str(e)}')
        return redirect('item_directory', identifier=identifier)

    return redirect('all_items')


