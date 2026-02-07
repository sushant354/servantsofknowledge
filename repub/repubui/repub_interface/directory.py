import os
import logging
import mimetypes

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .models import ProcessingJob
from .utils  import format_file_size

# Set up logger for this module
logger = logging.getLogger('repubui.directory')

@login_required
def job_output_directory(request, job_id, subpath=''):
    # Allow admin users to view any job, regular users can only view their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    
    # Get the base output directory path
    base_output_dir = job.get_output_dir()
    
    # Construct the current directory path
    if subpath:
        # Sanitize the subpath to prevent directory traversal
        subpath = subpath.strip('/')
        subpath_parts = [part for part in subpath.split('/') if part and part != '..']
        current_dir = os.path.join(base_output_dir, *subpath_parts)
        current_subpath = '/'.join(subpath_parts)
    else:
        current_dir = base_output_dir
        current_subpath = ''
    
    # Security check: ensure we're still within the job's output directory
    if not os.path.commonpath([base_output_dir, current_dir]) == base_output_dir:
        messages.error(request, 'Access denied: Invalid directory path.')
        return redirect('job_output_directory', job_id=job_id)
    
    if not os.path.exists(current_dir):
        messages.error(request, 'Directory does not exist.')
        return redirect('job_output_directory', job_id=job_id)
    
    # Build breadcrumb navigation
    breadcrumbs = [{'name': 'Output', 'path': ''}]
    if current_subpath:
        path_parts = current_subpath.split('/')
        for i, part in enumerate(path_parts):
            breadcrumb_path = '/'.join(path_parts[:i+1])
            breadcrumbs.append({'name': part, 'path': breadcrumb_path})
    
    # Get directory contents
    items = []
    try:
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
                'thumbnail_url': None,
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
                    
                # Check if this is an image and find corresponding thumbnail
                if mime_type and mime_type.startswith('image/'):
                    # Look for thumbnail in the thumbnails directory
                    thumbnails_dir = job.get_thumbnail_dir()
                        
                    thumb_path = os.path.join(settings.MEDIA_ROOT, thumbnails_dir, item_name)
                    if os.path.exists(thumb_path):
                        thumb_relative_path = os.path.relpath(thumb_path, settings.MEDIA_ROOT)
                        item_info['thumbnail_url'] = f"{settings.MEDIA_URL}{thumb_relative_path}"
                        
            items.append(item_info)
    except PermissionError:
        messages.error(request, 'Permission denied accessing directory.')
        return redirect('job_output_directory', job_id=job_id)
            
    # Separate directories and files
    directories = [item for item in items if item['is_directory']]
    files = [item for item in items if not item['is_directory']]
    
    context = {
        'job': job,
        'current_dir': current_dir,
        'current_subpath': current_subpath,
        'breadcrumbs': breadcrumbs,
        'directories': directories,
        'files': files,
        'total_items': len(items),
        'parent_path': '/'.join(current_subpath.split('/')[:-1]) if current_subpath and '/' in current_subpath else '' if current_subpath else None
    }
    
    return render(request, 'repub_interface/job_output_directory.html', context)


@login_required
def job_input_directory(request, job_id, subpath=''):
    # Allow admin users to view any job, regular users can only view their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    
    # Get the base input directory path
    base_input_dir = job.get_input_dir()
    
    # Construct the current directory path
    if subpath:
        # Sanitize the subpath to prevent directory traversal
        subpath = subpath.strip('/')
        subpath_parts = [part for part in subpath.split('/') if part and part != '..']
        current_dir = os.path.join(base_input_dir, *subpath_parts)
        current_subpath = '/'.join(subpath_parts)
    else:
        current_dir = base_input_dir
        current_subpath = ''
    
    # Security check: ensure we're still within the job's input directory
    if not os.path.commonpath([base_input_dir, current_dir]) == base_input_dir:
        messages.error(request, 'Access denied: Invalid directory path.')
        return redirect('job_input_directory', job_id=job_id)
    
    if not os.path.exists(current_dir):
        messages.error(request, 'Input directory does not exist.')
        return redirect('job_detail', job_id=job_id)
    
    # Build breadcrumb navigation
    breadcrumbs = [{'name': 'Input', 'path': ''}]
    if current_subpath:
        path_parts = current_subpath.split('/')
        for i, part in enumerate(path_parts):
            breadcrumb_path = '/'.join(path_parts[:i+1])
            breadcrumbs.append({'name': part, 'path': breadcrumb_path})
    
    # Get directory contents
    items = []
    try:
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
                'thumbnail_url': None,
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
                    
                # Check if this is an image and find corresponding thumbnail
                if mime_type and mime_type.startswith('image/'):
                    # Look for thumbnail in the thumbnails directory
                    thumbnails_dir = job.get_thumbnail_dir()
                        
                    thumb_path = os.path.join(settings.MEDIA_ROOT, thumbnails_dir, item_name)
                    if os.path.exists(thumb_path):
                        thumb_relative_path = os.path.relpath(thumb_path, settings.MEDIA_ROOT)
                        item_info['thumbnail_url'] = f"{settings.MEDIA_URL}{thumb_relative_path}"
                        
            items.append(item_info)
    except PermissionError:
        messages.error(request, 'Permission denied accessing input directory.')
        return redirect('job_input_directory', job_id=job_id)
            
    # Separate directories and files
    directories = [item for item in items if item['is_directory']]
    files = [item for item in items if not item['is_directory']]
    
    context = {
        'job': job,
        'current_dir': current_dir,
        'current_subpath': current_subpath,
        'breadcrumbs': breadcrumbs,
        'directories': directories,
        'files': files,
        'total_items': len(items),
        'parent_path': '/'.join(current_subpath.split('/')[:-1]) if current_subpath and '/' in current_subpath else '' if current_subpath else None,
        'is_input_directory': True  # Flag to differentiate in template
    }
    
    return render(request, 'repub_interface/job_input_directory.html', context)

