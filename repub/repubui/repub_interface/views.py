import os
import zipfile
import shutil
import threading
import cv2
import time
import logging
import mimetypes
import re
import csv
import datetime 

from PIL import Image
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.db import models
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe
from rest_framework.authtoken.models import Token
from .models import ProcessingJob, UserProfile
from .forms import ProcessingJobForm, UserRegistrationForm, ProcessingOptionsForm
from .tasks import derive_job_task

# Set up logger for this module
logger = logging.getLogger('repubui.views')

# Import functions from the original repub package
from repub import process_raw
from repub.imgfuncs.dewarp import dewarp
from repub.utils.scandir import Scandir
from repub.utils import pdfs
from .tasks import Args


max_deriving_jobs   = settings.MAX_DERIVING_JOBS

def authenticate_user(request):
    """Custom authentication that supports both session and token auth"""
    # First try session authentication (for web users)
    if request.user.is_authenticated:
        return request.user
    
    # Try token authentication (for API clients)
    auth_header = request.META.get('HTTP_AUTHORIZATION')
    if auth_header and auth_header.startswith('Token '):
        token = auth_header.split(' ')[1]
        try:
            token_obj = Token.objects.get(key=token)
            return token_obj.user
        except Token.DoesNotExist:
            pass
    
    return None


def login_or_token_required(view_func):
    """Decorator that requires either session login or valid token"""
    def wrapper(request, *args, **kwargs):
        user = authenticate_user(request)
        if user:
            # Set the user on the request for the view
            if not request.user.is_authenticated:
                request.user = user
            return view_func(request, *args, **kwargs)
        else:
            from django.contrib.auth.views import redirect_to_login
            from django.http import JsonResponse
            
            # For API requests, return JSON error
            if request.META.get('HTTP_AUTHORIZATION'):
                return JsonResponse({'success': False, 'error': 'Invalid token'}, status=401)
            
            # For web requests, redirect to login
            return redirect_to_login(request.get_full_path())
    
    return wrapper

@login_required
def all_jobs(request):
    # Staff users can see all jobs, regular users only see their own jobs
    if request.user.is_staff:
        jobs_list = ProcessingJob.objects.all()
    else:
        jobs_list = ProcessingJob.objects.filter(user=request.user)

    # Get sorting parameters
    sort_by = request.GET.get('sort_by', 'created_at')
    sort_order = request.GET.get('sort_order', 'desc')

    # Validate sort_by parameter
    allowed_sort_fields = ['created_at', 'processing_started_at']
    if sort_by not in allowed_sort_fields:
        sort_by = 'created_at'

    # Apply sorting
    if sort_order == 'asc':
        # For processing_started_at, nulls should be last when ascending
        if sort_by == 'processing_started_at':
            jobs_list = jobs_list.order_by(models.F('processing_started_at').asc(nulls_last=True))
        else:
            jobs_list = jobs_list.order_by(sort_by)
    else:
        # For processing_started_at, nulls should be last when descending too
        if sort_by == 'processing_started_at':
            jobs_list = jobs_list.order_by(models.F('processing_started_at').desc(nulls_last=True))
        else:
            jobs_list = jobs_list.order_by(f'-{sort_by}')

    # Get status filter from query parameters
    status_filter = request.GET.get('status')
    if status_filter and status_filter in ['pending', 'completed', 'processing', 'reviewing', 'failed', 'finalizing', 'preparing_review', 'derive_pending', 'deriving', 'derive_failed']:
        jobs_list = jobs_list.filter(status=status_filter)

    # Get search parameters
    title_query = request.GET.get('title', '').strip()
    identifier_query = request.GET.get('identifier', '').strip()
    author_query = request.GET.get('author', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    # Apply search filters
    if title_query:
        jobs_list = jobs_list.filter(title__icontains=title_query)

    if identifier_query:
        jobs_list = jobs_list.filter(identifier__icontains=identifier_query)

    if author_query:
        jobs_list = jobs_list.filter(author__icontains=author_query)

    if date_from:
        try:
            from_date = timezone.datetime.strptime(date_from, '%Y-%m-%d')
            from_date = timezone.make_aware(from_date, timezone.get_current_timezone())
            jobs_list = jobs_list.filter(created_at__gte=from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = timezone.datetime.strptime(date_to, '%Y-%m-%d')
            # Set to end of day
            to_date = to_date.replace(hour=23, minute=59, second=59)
            to_date = timezone.make_aware(to_date, timezone.get_current_timezone())
            jobs_list = jobs_list.filter(created_at__lte=to_date)
        except ValueError:
            pass

    paginator = Paginator(jobs_list, 20)  # Show 20 jobs per page
    page_number = request.GET.get('page')
    jobs = paginator.get_page(page_number)

    # Get all jobs for statistics (not filtered by status or search, but filtered by user if not staff)
    if request.user.is_staff:
        all_jobs_list = ProcessingJob.objects.all()
    else:
        all_jobs_list = ProcessingJob.objects.filter(user=request.user)

    context = {
        'jobs': jobs,
        'total_jobs': all_jobs_list.count(),
        'pending_jobs': all_jobs_list.filter(status='pending').count(),
        'completed_jobs': all_jobs_list.filter(status='completed').count(),
        'processing_jobs': all_jobs_list.filter(status='processing').count(),
        'failed_jobs': all_jobs_list.filter(status='failed').count(),
        'reviewing_jobs': all_jobs_list.filter(status='reviewing').count(),
        'derive_pending_jobs': all_jobs_list.filter(status='derive_pending').count(),
        'deriving_jobs': all_jobs_list.filter(status='deriving').count(),
        'derive_failed_jobs': all_jobs_list.filter(status='derive_failed').count(),
        'sort_by': sort_by,
        'sort_order': sort_order,
    }

    return render(request, 'repub_interface/all_jobs.html', context)


@login_required
def export_jobs_csv(request):
    """Export jobs as CSV based on search parameters"""
    # Get jobs based on user permissions
    if request.user.is_staff:
        jobs = ProcessingJob.objects.all()
    else:
        jobs = ProcessingJob.objects.filter(user=request.user)

    # Get search parameters (same as all_jobs view)
    status_filter = request.GET.get('status')
    if status_filter and status_filter in ['pending', 'completed', 'processing', 'reviewing', 'failed', 'finalizing', 'preparing_review', 'derive_pending', 'deriving', 'derive_failed']:
        jobs = jobs.filter(status=status_filter)

    title_query = request.GET.get('title', '').strip()
    identifier_query = request.GET.get('identifier', '').strip()
    author_query = request.GET.get('author', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    if title_query:
        jobs = jobs.filter(title__icontains=title_query)

    if identifier_query:
        jobs = jobs.filter(identifier__icontains=identifier_query)

    if author_query:
        jobs = jobs.filter(author__icontains=author_query)

    if date_from:
        try:
            from_date = timezone.datetime.strptime(date_from, '%Y-%m-%d')
            from_date = timezone.make_aware(from_date, timezone.get_current_timezone())
            jobs = jobs.filter(created_at__gte=from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = timezone.datetime.strptime(date_to, '%Y-%m-%d')
            to_date = to_date.replace(hour=23, minute=59, second=59)
            to_date = timezone.make_aware(to_date, timezone.get_current_timezone())
            jobs = jobs.filter(created_at__lte=to_date)
        except ValueError:
            pass

    # Order by created_at descending
    jobs = jobs.order_by('-created_at')

    # Build CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="jobs_export.csv"'

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)
    # Write header
    writer.writerow([
        'ID', 'Title', 'Identifier', 'Author', 'Status', 'Input Type',
        'Owner', 'Created At', 'Processing Started At',
        'Is Derived', 'Derived Identifier', 'Derived At'
    ])

    for job in jobs:
        writer.writerow([
            str(job.id),
            job.title or '',
            job.identifier or '',
            job.author or '',
            job.status,
            job.input_type,
            job.user.username if job.user else '',
            job.created_at.strftime('%Y-%m-%d %H:%M:%S') if job.created_at else '',
            job.processing_started_at.strftime('%Y-%m-%d %H:%M:%S') if job.processing_started_at else '',
            'Yes' if job.is_derived else 'No',
            job.derived_identifier or '',
            job.derived_at.strftime('%Y-%m-%d %H:%M:%S') if job.derived_at else '',
        ])

    return response


@csrf_exempt
@login_or_token_required
def home(request):
    jobs = ProcessingJob.objects.filter(user=request.user).order_by('-created_at')[:10]
    form = ProcessingJobForm()

    # Check if this is an API request (has Authorization header)
    is_api_request = bool(request.META.get('HTTP_AUTHORIZATION'))

    if request.method == 'POST':
        logger.debug(f"POST request received with FILES: {list(request.FILES.keys())}")
        logger.debug(f"POST data: {dict(request.POST)}")

        form = ProcessingJobForm(request.POST, request.FILES)
        logger.debug(f"Form is valid: {form.is_valid()}")

        if not form.is_valid():
            logger.debug(f"Form errors: {form.errors}")

            # For API requests, return JSON error response
            if is_api_request:
                error_messages = []
                for field, errors in form.errors.items():
                    field_name = field.replace('_', ' ').title() if field != "__all__" else "General"
                    for error in errors:
                        error_messages.append(f"{field_name}: {error}")

                return JsonResponse({
                    'success': False,
                    'error': 'Form validation failed',
                    'errors': error_messages,
                    'form_errors': dict(form.errors)
                }, status=400)

            # For web requests, render HTML with errors
            messages.error(request, "Please correct the errors below.")
            # Process field names for display
            form_errors_processed = {}
            for field, errors in form.errors.items():
                if field == "__all__":
                    display_field = "General"
                else:
                    display_field = field.replace('_', ' ').title()
                form_errors_processed[display_field] = errors
            return render(request, 'repub_interface/home.html', {
                'form': form,
                'jobs': jobs,
                'form_errors_processed': form_errors_processed
            })

        # First check if a file was actually uploaded
        if 'input_file' not in request.FILES:
            error_msg = 'No file was uploaded. Please select a file to upload.'
            form.add_error('input_file', error_msg)

            # For API requests, return JSON error
            if is_api_request:
                return JsonResponse({
                    'success': False,
                    'error': error_msg
                }, status=400)

            # For web requests, render HTML with errors
            messages.error(request, error_msg)
            return render(request, 'repub_interface/home.html', {
                'form': form,
                'jobs': jobs
            })

        # Save the form to create the job
        job = form.save(commit=False)
        job.user = request.user
        job.save()
        logger.info(f"Created job {job.id} with file: {job.input_file}")
        from .tasks import run_job_task
        run_job_task.delay(str(job.id))

        logger.info(f"Submitted processing job {job.id} to celery")
        messages.success(request, f'Job "{job.title or "Untitled"}" has been submitted and is being processed.')
        return redirect('job_detail', job_id=job.id)

    return render(request, 'repub_interface/home.html', {
        'form': form,
        'jobs': jobs
    })

@login_required
def job_detail(request, job_id):
    # Allow admin users to view any job, regular users can only view their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    
    # If the job is in reviewing status, redirect to the review page
    if job.status == 'reviewing':
        return redirect('job_review', job_id=job.id)
    
    # Handle processing options form submission
    if request.method == 'POST':
        form = ProcessingOptionsForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            messages.success(request, 'Processing options updated successfully.')
            return redirect('job_detail', job_id=job.id)
    else:
        form = ProcessingOptionsForm(instance=job)

    # Check if cleanup directories exist
    upload_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
    output_base_dir = job.get_output_dir()
    review_dir = job.get_review_dir()

    has_directories_to_cleanup = (
        os.path.exists(upload_base_dir) or
        os.path.exists(output_base_dir) or
        os.path.exists(review_dir)
    )

    return render(request, 'repub_interface/job_detail.html', {
        'job': job,
        'form': form,
        'has_directories_to_cleanup': has_directories_to_cleanup
    })

def generate_files_for_review(scandir, thumbdir, job):
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        filename =  os.path.basename(outfile)
        cv2.imwrite(outfile, img)

        thumbnail = process_raw.get_thumbnail(img)
        thumbfile = os.path.join(thumbdir, filename)
        cv2.imwrite(thumbfile, thumbnail)

def get_pagenum(filename):
    pagenum = None
    reobj = re.match('(?P<pagenum>\\d{4})\\.', filename)
    if reobj:
        groupdict = reobj.groupdict('pagenum')
        pagenum   = int(groupdict['pagenum'])
    return pagenum

def prepare_review_in_background(job):
    """Background thread function to prepare review files"""
    try:
        indir       = job.get_input_dir()
        reviewdir   = job.get_review_dir()

        imgdir   = os.path.join(reviewdir, 'images')
        thumbdir = os.path.join(reviewdir, 'thumbnails')

        os.makedirs(imgdir, exist_ok=True)
        os.makedirs(thumbdir, exist_ok=True)

        scandir  = Scandir(indir, imgdir, None)
        generate_files_for_review(scandir, thumbdir, job)

        job.status = 'reviewing'
        job.save()
        logger.info(f"Review preparation completed for job {job.id}")
    except Exception as e:
        logger.error(f"Error preparing review for job {job.id}: {str(e)}")
        job.status = job.status  # Keep previous status
        job.save()

@login_required
def job_review(request, job_id):
    # Allow admin users to start/view review for any job, regular users can only review their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    reviewdir   = job.get_review_dir()
    imgdir   = os.path.join(reviewdir, 'images')
    thumbdir = os.path.join(reviewdir, 'thumbnails')

    # If review directory doesn't exist, start preparation in background
    if not os.path.exists(reviewdir):
        # Check if job is already in reviewing or finalizing status to prevent race conditions
        if job.status == 'reviewing' or job.status == 'finalizing':
            logger.warning(f"Job {job.id} is already in {job.status} status. Skipping review directory generation.")
        else:
            # Set status to 'preparing_review' and start background preparation
            job.status = 'preparing_review'
            job.save()

            thread = threading.Thread(target=prepare_review_in_background, args=(job,))
            thread.start()

            messages.success(request, f'Preparing review for job "{job.title or "Untitled"}". Please wait...')
            return redirect('job_detail', job_id=job_id)

    # If we're still preparing, redirect back to job detail
    if job.status == 'preparing_review':
        return redirect('job_detail', job_id=job_id)

    outimgdir   = job.get_outimg_dir()
    outthumbdir = job.get_thumbnail_dir()
    pages = []
    for filename in os.listdir(imgdir):
        page = {}

        origimg = os.path.join(imgdir, filename)
        thumbfile = os.path.join(thumbdir, filename)

        relpath   = os.path.relpath(thumbfile, settings.MEDIA_ROOT)
        page['original_thumbnail'] = f"{settings.MEDIA_URL}{relpath}"

        relpath   = os.path.relpath(origimg, settings.MEDIA_ROOT)
        page['original_image'] = f"{settings.MEDIA_URL}{relpath}"

        croppedimg = os.path.join(outimgdir, filename)
        thumbfile  = os.path.join(outthumbdir, filename)

        relpath   = os.path.relpath(thumbfile, settings.MEDIA_ROOT)
        page['cropped_thumbnail'] = f"{settings.MEDIA_URL}{relpath}"

        relpath   = os.path.relpath(croppedimg, settings.MEDIA_ROOT)
        page['cropped_image'] = f"{settings.MEDIA_URL}{relpath}"

        page['page_number'] = get_pagenum(filename)
        page['filename']    = filename
        pages.append(page)

    pages.sort(key = lambda x: x['page_number'])

    # Add current timestamp for cache busting
    current_time = {'timestamp': int(time.time())}
    
    return render(request, 'repub_interface/job_review.html', {
        'job': job,
        'now': current_time,
        'pages': pages,
        'media_url': settings.MEDIA_URL
    })


@require_http_methods(["GET"])
@login_required
def page_editor(request, job_id, pagenum):
    """
    View for editing page crops with optimized loading and error handling.
    """
    if request.user.is_staff or request.user.is_superuser:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    page = {'page_number': pagenum}
    logger.warning ('page_editor: %s', page)

    reviewdir   = job.get_review_dir()
    imgdir      = os.path.join(reviewdir, 'images')
    filename    = f"{pagenum:04d}.jpg"
    origimg     = os.path.join(imgdir, filename)

    # Get processed/cropped image paths
    outimgdir   = job.get_outimg_dir()
    outthumbdir = job.get_thumbnail_dir()
    croppedimg  = os.path.join(outimgdir, filename)
    thumbfile   = os.path.join(outthumbdir, filename)

    next_page   = pagenum + 1
    next_img    = os.path.join(imgdir, f"{next_page:04d}.jpg")
    if not os.path.exists(next_img):
        next_page = None

    prev_page   = pagenum - 1
    prev_img    = os.path.join(imgdir, f"{prev_page:04d}.jpg")
    if not os.path.exists(prev_img):
        prev_page = None

    # Original image URL
    relpath     = os.path.relpath(origimg, settings.MEDIA_ROOT)
    page['original_image'] = f"{settings.MEDIA_URL}{relpath}"

    # Processed/cropped image URLs (if they exist)
    if os.path.exists(croppedimg):
        relpath = os.path.relpath(croppedimg, settings.MEDIA_ROOT)
        page['cropped_image'] = f"{settings.MEDIA_URL}{relpath}"
    else:
        page['cropped_image'] = None

    if os.path.exists(thumbfile):
        relpath = os.path.relpath(thumbfile, settings.MEDIA_ROOT)
        page['cropped_thumbnail'] = f"{settings.MEDIA_URL}{relpath}"
    else:
        page['cropped_thumbnail'] = None

    context = {
        'job': job,
        'page': page,
        'prev_page': prev_page,
        'next_page': next_page,
        'media_url': settings.MEDIA_URL,
        'now': timezone.now()
    }
    return render(request, 'repub_interface/page_editor.html', context)


@login_or_token_required
def job_download(request, job_id):
    # Allow admin to access any job, regular users only their own
    if request.user.is_staff or request.user.is_superuser:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    if job.output_file and job.status == 'completed':
        file_path = job.output_file.path
        response = FileResponse(open(file_path, 'rb'))
        response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
        return response
    return HttpResponse("File not available", status=404)


def create_thumbnail(image_path, max_size=(300, 300)):
    """Create a thumbnail of the given image"""
    try:
        img = Image.open(image_path)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        return img
    except Exception as e:
        logger.error(f"Error creating thumbnail for {image_path}: {str(e)}", exc_info=True)
        return None


@require_http_methods(["GET"])
@login_or_token_required
def job_status(request, job_id):
    """
    API endpoint for checking job status via AJAX.
    """
    try:
        # Allow admin users to check status of any job, regular users can only check their own jobs
        if request.user.is_staff:
            job = get_object_or_404(ProcessingJob, id=job_id)
        else:
            job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
        
        response_data = {
            'success': True,
            'status': job.status,
            'needs_review': getattr(job, 'needs_review', False),
            'job_id': str(job.id),
            'title': job.title or 'Untitled Job',
            'created_at': job.created_at.isoformat(),
        }
        
        # Add updated_at if it exists
        if hasattr(job, 'updated_at') and job.updated_at:
            response_data['updated_at'] = job.updated_at.isoformat()
        else:
            response_data['updated_at'] = job.created_at.isoformat()
            
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"Error getting job status for {job_id}: {str(e)}")
        return JsonResponse({
            'success': False,
            'status': 'error',
            'message': str(e)
        }, status=500)


@require_http_methods(["POST"])
@csrf_exempt
@login_required
def stop_job(request, job_id):
    """
    View to stop a processing job.
    """
    try:
        # Allow admin users to stop any job, regular users can only stop their own jobs
        if request.user.is_staff:
            job = get_object_or_404(ProcessingJob, id=job_id)
        else:
            job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

        if stop_single_job(job):
            if 'canceled' in job.error_message:
                messages.success(request, f'Job "{job.title or "Untitled"}" has been canceled.')
            else:
                messages.success(request, f'Job "{job.title or "Untitled"}" has been stopped.')
        else:
            messages.warning(request, f'Job "{job.title or "Untitled"}" cannot be stopped in its current state.')

        return redirect('job_detail', job_id=job.id)

    except Exception as e:
        logger.error(f"Error stopping job {job_id}: {str(e)}", exc_info=True)
        messages.error(request, 'An error occurred while stopping the job.')
        return redirect('job_detail', job_id=job_id)


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


def format_file_size(size_bytes):
    """Convert bytes to human readable format"""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f} {size_names[i]}"


def register(request):
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # User cannot login until email is confirmed
            user.save()
            
            # Create user profile
            profile, created = UserProfile.objects.get_or_create(user=user)
            
            # Send activation email
            send_activation_email(request, user)
            
            messages.success(request, 'Registration successful! Please check your email to activate your account.')
            return redirect('login')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'registration/register.html', {'form': form})


def send_activation_email(request, user):
    """Send activation email to user"""
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    
    activation_link = request.build_absolute_uri(
        reverse('activate_account', kwargs={'uidb64': uid, 'token': token})
    )
    
    subject = 'Activate Your REPUB Account'
    message = render_to_string('registration/activation_email.txt', {
        'user': user,
        'activation_link': activation_link,
    })
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )


def activate_account(request, uidb64, token):
    """Activate user account via email link"""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    
    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        
        # Update user profile
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.email_confirmed = True
        profile.save()
        
        messages.success(request, 'Your account has been activated successfully! You can now log in.')
        return redirect('login')
    else:
        messages.error(request, 'The activation link is invalid or has expired.')
        return redirect('register')


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def save_snip(request, job_id, page_number):
    """Save snipped coordinates and modify the output image directly"""
    # Get the job
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    
        
    reviewdir   = job.get_review_dir()
    outimgdir   = job.get_outimg_dir()
    outthumbdir = job.get_thumbnail_dir()

    filename   = f"{page_number:04d}.jpg"
    imgdir     = os.path.join(reviewdir, 'images')
    reviewimg  = os.path.join(imgdir, filename)
    outimg     = os.path.join(outimgdir, filename)
    thumbfile  = os.path.join(outthumbdir, filename)

    # Get coordinates from request
    x = int(request.POST.get('x', 0))
    y = int(request.POST.get('y', 0))
    width = int(request.POST.get('width', 0))
    height = int(request.POST.get('height', 0))
    rotation = int(request.POST.get('rotation', 0))
    dewarp_enabled = request.POST.get('dewarp', 'false').lower() == 'true'

    if width <= 0 or height <= 0:
        return JsonResponse({
            'status': 'error',
            'message': 'Invalid selection dimensions'
        })

    image = cv2.imread(reviewimg)
    if image is None:
        return JsonResponse({
            'status': 'error',
            'message': 'Could not load image'
        })

    # Apply rotation if needed
    if rotation != 0:
        if rotation == 90:
            image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            image = cv2.rotate(image, cv2.ROTATE_180)
        elif rotation == 270:
            image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        logger.info(f"Rotated image by {rotation}° for job {job.id}, page {page_number}")

    # Validate coordinates are within image bounds (after rotation)
    img_height, img_width = image.shape[:2]
    if x < 0 or y < 0 or x + width > img_width or y + height > img_height:
        return JsonResponse({
            'status': 'error',
            'message': f'Selection exceeds image bounds ({img_width}x{img_height})'
        })

    # Crop the image using the provided coordinates
    cropped_image = image[y:y+height, x:x+width]

    # Apply dewarping if enabled
    if dewarp_enabled:
        try:
            cropped_image = dewarp(cropped_image, logger=logger)
            logger.info(f"Applied dewarping to snip for job {job.id}, page {page_number}")
        except Exception as e:
            logger.warning(f"Dewarping failed for job {job.id}, page {page_number}: {str(e)}")

    img = process_raw.resize_image(cropped_image, job.reduce_factor)
    thumb_image = process_raw.get_thumbnail(cropped_image)

    cv2.imwrite(thumbfile, thumb_image)
    cv2.imwrite(outimg, img)

    rotation_info = f", rotation={rotation}°" if rotation != 0 else ""
    logger.info(f"Saved snip for job {job.id}, page {page_number}: {x},{y} {width}x{height}{rotation_info} to {outimg}")

    # Generate URL for the output image
    outimg_relpath = os.path.relpath(outimg, settings.MEDIA_ROOT)
    outimg_url = f"{settings.MEDIA_URL}{outimg_relpath}"

    # Generate URL for the thumbnail
    thumbfile_relpath = os.path.relpath(thumbfile, settings.MEDIA_ROOT)
    thumb_url = f"{settings.MEDIA_URL}{thumbfile_relpath}"

    return JsonResponse({
        'status': 'success',
        'message': 'Snip saved successfully',
        'coordinates': {'x': x, 'y': y, 'width': width, 'height': height},
        'output_path': outimg,
        'output_image_url': outimg_url,
        'thumbnail_url': thumb_url
    })


def run_finalize_job(job):
    """Background thread function to finalize the job"""
    try:
        # Create a logger for the finalization process
        output_dir = job.get_output_dir()
        logfile = os.path.join(output_dir, 'processing.log')
        loghandle = open(logfile, 'a', encoding='utf-8')

        finalize_logger = logging.getLogger(f'repub.finalize.{job.id}')

        # Clear any existing handlers
        finalize_logger.handlers.clear()

        # Prevent propagation to parent loggers to avoid writing to closed streams
        # in uwsgi background threads
        finalize_logger.propagate = False

        # Create file handler
        file_handler = logging.StreamHandler(loghandle)

        # Add handler to logger
        finalize_logger.addHandler(file_handler)

        finalize_logger.info(f"Starting finalization for job {job.id}")

        input_dir = job.get_input_dir()
        args = Args(job, input_dir, output_dir)
        scandir = Scandir(args.indir, args.outdir, args.pagenums, finalize_logger)

        input_file_path = job.input_file.path

        if job.input_type == 'pdf':
            metadata = pdfs.get_metadata(input_file_path)
        else:
            metadata = scandir.metadata

        outfiles = []
        imgdir = job.get_outimg_dir()

        width = 0
        num = 0

        for filename in os.listdir(imgdir):
            outfile = os.path.join(imgdir, filename)
            img     = cv2.imread(outfile)
            (h, w)  = img.shape[:2]
            width  += w 
            num    += 1
        if num > 0:
            avg_width = int(width/num)
            for filename in os.listdir(imgdir):
                outfile = os.path.join(imgdir, filename)
                img     = cv2.imread(outfile)
                img     = adjust_width(img, avg_width)
                cv2.imwrite(outfile, img)

        for filename in os.listdir(imgdir):
            outfile = os.path.join(imgdir, filename)
            pagenum = get_pagenum(filename)
            outfiles.append((pagenum, outfile))

        outfiles.sort(key=lambda x: x[0])
        finalize_logger.info(f"Finalizing PDF with {len(outfiles)} pages")

        pdfs.save_pdf(outfiles, metadata, args.langs, args.outpdf,
                      args.do_ocr, args.outhocr, args.outtxt, finalize_logger)

        relative_path = os.path.relpath(args.outpdf, settings.MEDIA_ROOT)
        job.output_file = relative_path
        job.status = 'completed'
        job.save()

        reviewdir = job.get_review_dir()
        shutil.rmtree(reviewdir)
        loghandle.close()

    except Exception as e:
        logger.error(f"Error finalizing job {job.id}: {str(e)}")
        job.status = 'reviewing'
        job.save()

def adjust_width(img, avg_width):
    (h, w) = img.shape[:2]

    height = int (avg_width/w * h)
    dim    = (avg_width, height)
    return cv2.resize(img, dim, interpolation = cv2.INTER_AREA)

@csrf_exempt
@login_required
@require_http_methods(["POST"])
def finalize_job(request, job_id):
    """Finalize the job after review"""
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    # Check if job is already being finalized to prevent duplicate submissions
    if job.status == 'finalizing':
        logger.warning(f"Job {job.id} is already being finalized. Ignoring duplicate request.")
        return redirect('job_detail', job_id=job_id)

    # Set job status to finalizing
    job.status = 'finalizing'
    job.save()

    # Start finalization in background thread
    thread = threading.Thread(target=run_finalize_job, args=(job,))
    thread.start()

    logger.info(f"Started finalization for job {job.id} in background thread")
    messages.success(request, f'Job "{job.title or "Untitled"}" is being finalized.')

    return redirect('job_detail', job_id=job_id)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def reject_review(request, job_id):
    """Reject the review and go back to job details"""
    # Allow admin users to reject any job's review, regular users can only reject their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    # Check if job is not in reviewing status to prevent duplicate submissions
    reviewdir = job.get_review_dir()
    if not os.path.exists(reviewdir):
        logger.warning(f"Job {job.id} is not in reviewing status. Current status: {job.status}. Ignoring reject request.")
        return redirect('job_detail', job_id=job_id)

    shutil.rmtree(reviewdir)

    job.status = 'completed'
    job.save()

    return redirect('job_detail', job_id=job_id)


def stop_single_job(job):
    """
    Helper function to stop a single job.
    Sets job status to failed with appropriate error message.

    Args:
        job: ProcessingJob instance to stop

    Returns:
        bool: True if job was stopped, False if job cannot be stopped

    Raises:
        Exception: If there's an error during stop
    """
    if job.status not in ['pending', 'processing', 'finalizing', 'derive_pending', 'deriving']:
        return False

    original_status = job.status

    # Derive jobs should be set to derive_failed, regular jobs to failed
    if original_status in ['derive_pending', 'deriving']:
        job.status = 'derive_failed'
    else:
        job.status = 'failed'

    if original_status in ['pending', 'derive_pending']:
        job.error_message = 'Job canceled by user'
    else:
        job.error_message = 'Job stopped by user'

    job.save()
    return True


def retry_single_job(job):
    """
    Helper function to retry a single job.
    Cleans up output directory and starts the job processing in a background thread.

    Args:
        job: ProcessingJob instance to retry

    Raises:
        Exception: If there's an error during retry setup
    """
    job.status = 'pending'
    job.error_message = ''
    job.save()

    output_dir = job.get_output_dir()
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

        img_dir = os.path.join(output_dir, 'output')
        os.makedirs(img_dir, exist_ok=True)

        thumbnaildir = os.path.join(output_dir, 'thumbnails')
        os.makedirs(thumbnaildir, exist_ok=True)

    from .tasks import run_job_task
    run_job_task.delay(str(job.id))


@login_required
@require_http_methods(["POST"])
def retry_job(request, job_id):
    """Retry job with same settings"""
    # Allow admin users to retry any job, regular users can only retry their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    try:
        retry_single_job(job)
        messages.success(request, f'Job "{job.title or "Untitled"}" has been queued for retry.')
    except Exception as e:
        logger.error(f"Error retrying job {job.id}: {str(e)}", exc_info=True)
        messages.error(request, f'Error retrying job: {str(e)}')

    return redirect('job_detail', job_id=job_id)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def bulk_retry_jobs(request):
    """Retry multiple selected failed jobs"""
    job_ids = request.POST.getlist('job_ids')

    if not job_ids:
        messages.warning(request, 'No jobs selected for retry.')
        return redirect('all_jobs')

    # Filter jobs based on user permissions
    if request.user.is_staff:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status='failed')
    else:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status='failed', user=request.user)

    retry_count = 0
    failed_count = 0

    for job in jobs:
        try:
            retry_single_job(job)
            retry_count += 1
            logger.info(f"User {request.user.username} retried job {job.id}")
        except Exception as e:
            failed_count += 1
            logger.error(f"Error retrying job {job.id}: {str(e)}", exc_info=True)
            messages.error(request, f'Error retrying job "{job.title or job.id}": {str(e)}')

    if retry_count > 0:
        messages.success(request, f'Successfully queued {retry_count} job{"s" if retry_count > 1 else ""} for retry.')

    if failed_count > 0:
        messages.warning(request, f'{failed_count} job{"s" if failed_count > 1 else ""} could not be retried.')

    if retry_count == 0 and failed_count == 0:
        messages.warning(request, 'No jobs were retried. Please ensure selected jobs are in failed status.')

    return redirect('all_jobs')


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def bulk_stop_jobs(request):
    """Stop multiple selected processing/pending/derive_pending jobs"""
    job_ids = request.POST.getlist('job_ids')

    if not job_ids:
        messages.warning(request, 'No jobs selected to stop.')
        return redirect('all_jobs')

    # Filter jobs based on user permissions
    if request.user.is_staff:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status__in=['pending', 'processing', 'finalizing', 'derive_pending', 'deriving'])
    else:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status__in=['pending', 'processing', 'finalizing', 'derive_pending', 'deriving'], user=request.user)

    stop_count = 0
    failed_count = 0

    for job in jobs:
        try:
            if stop_single_job(job):
                stop_count += 1
                logger.info(f"User {request.user.username} stopped job {job.id}")
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"Error stopping job {job.id}: {str(e)}", exc_info=True)
            messages.error(request, f'Error stopping job "{job.title or job.id}": {str(e)}')

    if stop_count > 0:
        messages.success(request, f'Successfully stopped {stop_count} job{"s" if stop_count > 1 else ""}.')

    if failed_count > 0:
        messages.warning(request, f'{failed_count} job{"s" if failed_count > 1 else ""} could not be stopped.')

    if stop_count == 0 and failed_count == 0:
        messages.warning(request, 'No jobs were stopped. Please ensure selected jobs are in processing, pending, finalizing, derive_pending, or deriving status.')

    return redirect('all_jobs')


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def bulk_set_derive_failed(request):
    """Set multiple selected deriving jobs to derive_failed status"""
    job_ids = request.POST.getlist('job_ids')

    if not job_ids:
        messages.warning(request, 'No jobs selected.')
        return redirect('all_jobs')

    # Filter jobs based on user permissions
    if request.user.is_staff:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status='deriving')
    else:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status='deriving', user=request.user)

    updated_count = 0
    failed_count = 0

    for job in jobs:
        try:
            job.status = 'derive_failed'
            job.error_message = 'Manually set to derive_failed by user.'
            job.save()
            updated_count += 1
            logger.info(f"User {request.user.username} set job {job.id} to derive_failed")
        except Exception as e:
            failed_count += 1
            logger.error(f"Error setting job {job.id} to derive_failed: {str(e)}", exc_info=True)
            messages.error(request, f'Error updating job "{job.title or job.id}": {str(e)}')

    if updated_count > 0:
        messages.success(request, f'Successfully set {updated_count} job{"s" if updated_count > 1 else ""} to derive_failed.')

    if failed_count > 0:
        messages.warning(request, f'{failed_count} job{"s" if failed_count > 1 else ""} could not be updated.')

    if updated_count == 0 and failed_count == 0:
        messages.warning(request, 'No jobs were updated. Please ensure selected jobs are in deriving status.')

    return redirect('all_jobs')


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def bulk_derive_jobs(request):
    """Derive multiple selected completed jobs"""
    job_ids = request.POST.getlist('job_ids')

    if not job_ids:
        messages.warning(request, 'No jobs selected for derivation.')
        return redirect('all_jobs')

    # Filter jobs based on user permissions
    if request.user.is_staff:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status__in=['completed', 'derive_failed', 'deriving'])
    else:
        jobs = ProcessingJob.objects.filter(id__in=job_ids, status__in=['completed', 'derive_failed', 'deriving'], user=request.user)

    derive_count = 0
    failed_count = 0

    for job in jobs:
        try:
            # Check if job is already queued to prevent duplicate submissions
            if job.status == 'derive_pending':
                logger.warning(f"Job {job.id} is already queued for derivation. Skipping.")
                failed_count += 1
                continue

            # Set job status to derive_pending
            job.status = 'derive_pending'
            job.error_message = ''
            job.save()

            # Enqueue derivation as a Celery task
            derive_job_task.delay(str(job.id))

            derive_count += 1
            logger.info(f"User {request.user.username} started derivation for job {job.id}")
        except Exception as e:
            failed_count += 1
            logger.error(f"Error starting derivation for job {job.id}: {str(e)}", exc_info=True)
            messages.error(request, f'Error deriving job "{job.title or job.id}": {str(e)}')

    if derive_count > 0:
        messages.success(request, f'Successfully started derivation for {derive_count} job{"s" if derive_count > 1 else ""}.')

    if failed_count > 0:
        messages.warning(request, f'{failed_count} job{"s" if failed_count > 1 else ""} could not be derived.')

    if derive_count == 0 and failed_count == 0:
        messages.warning(request, 'No jobs were derived. Please ensure selected jobs are in completed, derive_failed, or deriving status.')

    return redirect('all_jobs')


@login_required
def api_token_management(request):
    """Manage user's API token for REST framework authentication"""
    token = Token.objects.filter(user=request.user).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'generate':
            if not token:
                token = Token.objects.create(user=request.user)
                messages.success(request, 'API token has been generated successfully!')
                logger.info(f"Generated new API token for user {request.user.username}")
            else:
                messages.info(request, 'You already have an API token. Use "Regenerate" to create a new one.')

        elif action == 'regenerate':
            if token:
                token.delete()
            token = Token.objects.create(user=request.user)
            messages.success(request, 'API token has been regenerated successfully! Make sure to update any applications using the old token.')
            logger.info(f"Regenerated API token for user {request.user.username}")

        elif action == 'delete':
            if token:
                token.delete()
                token = None
                messages.success(request, 'API token has been deleted successfully.')
                logger.info(f"Deleted API token for user {request.user.username}")
            else:
                messages.info(request, 'No API token to delete.')

        return redirect('api_token')

    context = {
        'token': token,
    }

    return render(request, 'repub_interface/api_token.html', context)


@login_required
@require_http_methods(["POST"])
def derive_job(request, job_id):
    """Derive a job by creating a directory with identifier and copying all relevant files"""
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    if job.status not in ['completed', 'derive_failed']:
        messages.error(request, 'Can only derive completed jobs or retry failed derivations.')
        return redirect('job_detail', job_id=job_id)

    # Check if job is already being derived to prevent duplicate submissions
    if job.status in ['deriving', 'derive_pending']:
        logger.warning(f"Job {job.id} is already being derived. Ignoring duplicate request.")
        return redirect('job_detail', job_id=job_id)

    # Extract derive_reduce_factor from POST request
    derive_reduce_factor = request.POST.get('derive_reduce_factor', '').strip()
    if derive_reduce_factor:
        try:
            derive_reduce_factor = float(derive_reduce_factor)
            if derive_reduce_factor <= 0 or derive_reduce_factor > 1:
                messages.error(request, 'Reduce factor must be between 0 and 1.')
                return redirect('job_detail', job_id=job_id)
        except ValueError:
            messages.error(request, 'Invalid reduce factor value.')
            return redirect('job_detail', job_id=job_id)
    else:
        derive_reduce_factor = None

    # Set job status to derive_pending
    job.status = 'derive_pending'
    job.derive_reduce_factor = derive_reduce_factor
    job.error_message = ''
    job.save()

    # Enqueue derivation as a Celery task
    derive_job_task.delay(str(job.id))

    logger.info(f"Enqueued derivation for job {job.id} with reduce_factor={derive_reduce_factor}")
    messages.success(request, f'Job "{job.title or "Untitled"}" is being derived.')

    return redirect('job_detail', job_id=job_id)

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


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def cleanup_job(request, job_id):
    """Clean up input and output directories for a job without deleting the job from database"""
    # Allow admin users to cleanup any job, regular users can only cleanup their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    # Don't allow cleanup if job is currently processing
    if job.status in ['processing', 'finalizing', 'deriving', 'preparing_review']:
        messages.error(request, f'Cannot cleanup job while it is in {job.status} status.')
        return redirect('job_detail', job_id=job_id)

    directories_cleaned = []
    errors = []

    # Clean up input directory
    upload_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
    if os.path.exists(upload_base_dir):
        try:
            shutil.rmtree(upload_base_dir)
            directories_cleaned.append(f'Input directory (uploads/{job.id})')
            logger.info(f"User {request.user.username} cleaned up input directory for job {job.id}")
        except Exception as e:
            error_msg = f'Error cleaning input directory: {str(e)}'
            errors.append(error_msg)
            logger.error(f"Error cleaning input directory for job {job.id}: {str(e)}", exc_info=True)

    # Clean up output directory
    output_base_dir = job.get_output_dir()
    if os.path.exists(output_base_dir):
        try:
            shutil.rmtree(output_base_dir)
            directories_cleaned.append(f'Output directory (processed/{job.id})')
            logger.info(f"User {request.user.username} cleaned up output directory for job {job.id}")
        except Exception as e:
            error_msg = f'Error cleaning output directory: {str(e)}'
            errors.append(error_msg)
            logger.error(f"Error cleaning output directory for job {job.id}: {str(e)}", exc_info=True)

    # Clean up review directory if it exists
    review_dir = job.get_review_dir()
    if os.path.exists(review_dir):
        try:
            shutil.rmtree(review_dir)
            directories_cleaned.append(f'Review directory (review/{job.id})')
            logger.info(f"User {request.user.username} cleaned up review directory for job {job.id}")
        except Exception as e:
            error_msg = f'Error cleaning review directory: {str(e)}'
            errors.append(error_msg)
            logger.error(f"Error cleaning review directory for job {job.id}: {str(e)}", exc_info=True)

    # Display results to user
    if directories_cleaned:
        cleaned_list = '<br>'.join([f'- {d}' for d in directories_cleaned])
        messages.success(request, mark_safe(f'Successfully cleaned up directories for job "{job.title or "Untitled"}":<br>{cleaned_list}'))
    else:
        messages.info(request, f'No directories found to clean up for job "{job.title or "Untitled"}".')

    if errors:
        error_list = '<br>'.join([f'- {e}' for e in errors])
        messages.error(request, mark_safe(f'Some errors occurred during cleanup:<br>{error_list}'))

    return redirect('job_detail', job_id=job_id)


@login_required
@require_http_methods(["POST"])
@csrf_exempt
def delete_job(request, job_id):
    """Delete a job completely including all its files and database record"""
    # Allow admin users to delete any job, regular users can only delete their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    # Don't allow deletion if job is currently processing
    if job.status in ['processing', 'finalizing', 'deriving', 'preparing_review']:
        messages.error(request, f'Cannot delete job while it is in {job.status} status.')
        return redirect('job_detail', job_id=job_id)

    job_title = job.title or "Untitled"
    directories_deleted = []
    errors = []

    # Delete input directory
    upload_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
    if os.path.exists(upload_base_dir):
        try:
            shutil.rmtree(upload_base_dir)
            directories_deleted.append(f'Input directory (uploads/{job.id})')
            logger.info(f"User {request.user.username} deleted input directory for job {job.id}")
        except Exception as e:
            error_msg = f'Error deleting input directory: {str(e)}'
            errors.append(error_msg)
            logger.error(f"Error deleting input directory for job {job.id}: {str(e)}", exc_info=True)

    # Delete output directory
    output_base_dir = job.get_output_dir()
    if os.path.exists(output_base_dir):
        try:
            shutil.rmtree(output_base_dir)
            directories_deleted.append(f'Output directory (processed/{job.id})')
            logger.info(f"User {request.user.username} deleted output directory for job {job.id}")
        except Exception as e:
            error_msg = f'Error deleting output directory: {str(e)}'
            errors.append(error_msg)
            logger.error(f"Error deleting output directory for job {job.id}: {str(e)}", exc_info=True)

    # Delete review directory if it exists
    review_dir = job.get_review_dir()
    if os.path.exists(review_dir):
        try:
            shutil.rmtree(review_dir)
            directories_deleted.append(f'Review directory (review/{job.id})')
            logger.info(f"User {request.user.username} deleted review directory for job {job.id}")
        except Exception as e:
            error_msg = f'Error deleting review directory: {str(e)}'
            errors.append(error_msg)
            logger.error(f"Error deleting review directory for job {job.id}: {str(e)}", exc_info=True)

    # Delete the job from database
    try:
        job.delete()
        logger.info(f"User {request.user.username} deleted job {job_id} ({job_title}) from database")

        # Show success message
        if directories_deleted:
            messages.success(request, f'Job "{job_title}" has been deleted successfully along with all associated files.')
        else:
            messages.success(request, f'Job "{job_title}" has been deleted successfully.')

    except Exception as e:
        error_msg = f'Error deleting job from database: {str(e)}'
        errors.append(error_msg)
        logger.error(f"Error deleting job {job_id} from database: {str(e)}", exc_info=True)
        messages.error(request, mark_safe(f'Failed to delete job from database:<br>{error_msg}'))
        return redirect('job_detail', job_id=job_id)

    # Display any errors that occurred during file deletion
    if errors:
        error_list = '<br>'.join([f'- {e}' for e in errors])
        messages.warning(request, mark_safe(f'Job deleted, but some errors occurred during file cleanup:<br>{error_list}'))

    return redirect('all_jobs')
