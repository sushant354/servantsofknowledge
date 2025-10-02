import os
import zipfile
import shutil
import threading
import cv2
import time
import logging
import mimetypes
import re

from PIL import Image
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
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
from rest_framework.authtoken.models import Token
from .models import ProcessingJob, UserProfile
from .forms import ProcessingJobForm, UserRegistrationForm, ProcessingOptionsForm

# Set up logger for this module
logger = logging.getLogger('repubui.views')

# Import functions from the original repub package
from repub import process_raw
from repub.imgfuncs.dewarp import dewarp
from repub.utils.scandir import Scandir
from repub.utils import pdfs


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
    jobs_list = ProcessingJob.objects.filter(user=request.user).order_by('-created_at')
    
    # Get status filter from query parameters
    status_filter = request.GET.get('status')
    if status_filter and status_filter in ['completed', 'processing', 'reviewing', 'failed', 'finalizing']:
        jobs_list = jobs_list.filter(status=status_filter)
    
    paginator = Paginator(jobs_list, 10)  # Show 10 jobs per page
    page_number = request.GET.get('page')
    jobs = paginator.get_page(page_number)
    
    # Get all jobs for statistics (not filtered)
    all_jobs_list = ProcessingJob.objects.filter(user=request.user)
    
    context = {
        'jobs': jobs,
        'total_jobs': all_jobs_list.count(),
        'completed_jobs': all_jobs_list.filter(status='completed').count(),
        'processing_jobs': all_jobs_list.filter(status='processing').count(),
        'failed_jobs': all_jobs_list.filter(status='failed').count(),
        'reviewing_jobs': all_jobs_list.filter(status='reviewing').count(),
    }
    
    return render(request, 'repub_interface/all_jobs.html', context)


@csrf_exempt
@login_or_token_required
def home(request):
    jobs = ProcessingJob.objects.filter(user=request.user).order_by('-created_at')[:10]
    form = ProcessingJobForm()

    if request.method == 'POST':
        logger.debug(f"POST request received with FILES: {list(request.FILES.keys())}")
        logger.debug(f"POST data: {dict(request.POST)}")
        
        form = ProcessingJobForm(request.POST, request.FILES)
        logger.debug(f"Form is valid: {form.is_valid()}")
        
        if not form.is_valid():
            logger.debug(f"Form errors: {form.errors}")
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
            form.add_error('input_file', 'No file was uploaded. Please select a file to upload.')
            messages.error(request, "No file was uploaded. Please select a file to upload.")
            return render(request, 'repub_interface/home.html', {
                'form': form,
                'jobs': jobs
            })

        # Save the form to create the job
        job = form.save(commit=False)
        job.user = request.user
        job.save()
        logger.info(f"Created job {job.id} with file: {job.input_file}")
        run_and_monitor_job(job)

        logger.info(f"Started processing job {job.id} in background thread")
        messages.success(request, f'Job "{job.title or "Untitled"}" has been submitted and is being processed.')
        return redirect('job_detail', job_id=job.id)

    return render(request, 'repub_interface/home.html', {
        'form': form,
        'jobs': jobs
    })

def run_and_monitor_job(job):
    # Start processing in background thread
    thread = threading.Thread(target=run_job, args=(job,))
    thread.start()

def run_job(job):
    logger = logging.getLogger('repubui')
    try:
        process_job(job)
    except Exception as e:
        logger.exception('Error in process_job %s error: %s', job.id, e)
        job.status = 'failed'
        job.save()

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
        
    return render(request, 'repub_interface/job_detail.html', {
        'job': job,
        'form': form
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

@login_required
def job_review(request, job_id):
    # Allow admin users to start/view review for any job, regular users can only review their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    indir       = job.get_input_dir()
    reviewdir   = job.get_review_dir()
    outimgdir   = job.get_outimg_dir()
    outthumbdir = job.get_thumbnail_dir()

    imgdir   = os.path.join(reviewdir, 'images')
    thumbdir = os.path.join(reviewdir, 'thumbnails')

    if not os.path.exists(reviewdir):
        os.makedirs(imgdir, exist_ok=True)
        os.makedirs(thumbdir, exist_ok=True)

        scandir  = Scandir(indir, imgdir, None)
        generate_files_for_review(scandir, thumbdir, job)
        job.status = 'reviewing'
        job.save()

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
    origimg     = os.path.join(imgdir, f"{pagenum:04d}.jpg")

    next_page   = pagenum + 1
    next_img    = os.path.join(imgdir, f"{next_page:04d}.jpg")
    if not os.path.exists(next_img):
        next_page = None

    prev_page   = pagenum - 1
    prev_img    = os.path.join(imgdir, f"{prev_page:04d}.jpg")
    if not os.path.exists(prev_img):
        prev_page = None

    relpath     = os.path.relpath(origimg, settings.MEDIA_ROOT)
    page['original_image'] = f"{settings.MEDIA_URL}{relpath}"
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


class Args:
    def __init__(self, job, input_dir, output_dir):
        img_dir = os.path.join(output_dir, 'output')
        os.makedirs(img_dir, exist_ok=True)

        thumbnaildir = os.path.join(output_dir, 'thumbnails')
        os.makedirs(thumbnaildir, exist_ok=True)

        self.indir  = input_dir
        self.outdir = img_dir
        self.outpdf = os.path.join(output_dir, "x_final.pdf")
        self.langs  = job.language

        self.thumbnaildir = thumbnaildir
        self.thumbnail    = os.path.join(output_dir, '__ia_thumb.jpg')
        self.outhocr      = os.path.join(output_dir, 'x_hocr.html.gz')
        self.outtxt       = os.path.join(output_dir, 'x_text.txt')

        self.maxcontours  = job.maxcontours
        self.xmax         = job.xmaximum
        self.ymax         = job.ymax
        self.mingray      = job.mingray
        self.crop         = job.crop
        self.deskew       = job.deskew
        self.do_ocr       = job.ocr

        self.dewarp       = job.dewarp
        self.drawcontours = job.draw_contours
        self.gray         = job.gray
        self.rotate_type  = job.rotate_type
        self.factor       = job.reduce_factor
        self.pagenums     = None

def process_job(job):
    job.status = 'processing'
    job.output_file = None
    job.save()

    # Get the input and output directories/files
    input_file_path = job.input_file.path if job.input_file else None
    input_dir       = job.get_input_dir()
    output_dir      = job.get_output_dir()

    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    logfile   = os.path.join(output_dir, 'processing.log')
    loghandle = open(logfile, 'a', encoding='utf-8')

    # Create a logger that writes to the log file
    job_logger = logging.getLogger('repub.job')

    # Clear any existing handlers
    job_logger.handlers.clear()

    # Create file handler
    file_handler = logging.StreamHandler(loghandle)

    # Add handler to logger
    job_logger.addHandler(file_handler)

    job_logger.info(f"Processing job ID: {job.id}")
    job_logger.info(f"Input file: {input_file_path}")
    job_logger.info(f"Input file exists: {os.path.exists(input_file_path) if input_file_path else False}")
    job_logger.info(f"Input directory: {input_dir}")

    # Process based on input type
    if job.input_type == 'pdf':
        job_logger.info("Input type: PDF")
        if not input_file_path or not os.path.exists(input_file_path):
            raise ValueError(f"PDF file not found at: {input_file_path}")
        # Extract images from PDF
        pdfs.pdf_to_images(input_file_path, input_dir)
    elif job.input_type == 'images':
        job_logger.info("Input type: Images")
        if not input_file_path or not os.path.exists(input_file_path):
            raise ValueError(f"Image file not found at: {input_file_path}")
        # If it's a ZIP file, extract it
        if input_file_path.lower().endswith('.zip'):
            job_logger.info("Extracting ZIP file")
            with zipfile.ZipFile(input_file_path, 'r') as zip_ref:
                zip_ref.extractall(input_dir)

            # List all extracted files for debugging
            n = 0
            for root, dirs, files in os.walk(input_dir):
                for file in files:
                    n += 1
            job_logger.info(f"Extracted {n} files from ZIP")

            # If it's a single image, copy it to the input directory
        elif input_file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
            job_logger.info("Processing single image file")
            filename = os.path.basename(input_file_path)
            destination = os.path.join(input_dir, filename)
            shutil.copy(input_file_path, destination)

    args = Args(job, input_dir, output_dir)
    scandir = Scandir(args.indir, args.outdir, args.pagenums, job_logger)
    if job.input_type == 'pdf':
        metadata = pdfs.get_metadata(input_file_path)
    else:
        metadata = scandir.metadata
    title = metadata.get('/Title')
    if title:
        job.title = title
        job.save()    

    if args.drawcontours:
        args.thumbnaildir = job.get_thumbnail_dir()
        outfiles = process_raw.draw_contours(scandir, args, job_logger)
    elif args.gray:
        args.thumbnaildir = job.get_thumbnail_dir()
        outfiles = process_raw.gray_images(scandir, args, job_logger)
    elif args.deskew and not args.crop:
        outfiles = process_raw.deskew_images(scandir, args, job_logger)
    else:
        outfiles = process_raw.process_images(scandir, args, job_logger)
        if args.outpdf:
            pdfs.save_pdf(outfiles, metadata, args.langs, args.outpdf, \
                          args.do_ocr, args.outhocr, args.outtxt, job_logger)
            relative_path = os.path.relpath(args.outpdf, settings.MEDIA_ROOT)
            job.output_file = relative_path

    job.status = 'completed'
    job.save()

    # Close the log file handle
    loghandle.close()

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
        
        # Only allow stopping jobs that are currently processing
        if job.status in ['processing', 'finalizing']:
            job.status = 'failed'
            job.error_message = 'Job stopped by user'
            job.save()
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
        finalize_logger.info(f"Starting finalization for job {job.id}")
        # Clear any existing handlers
        finalize_logger.handlers.clear()

        # Create file handler
        file_handler = logging.StreamHandler(loghandle)

        # Add handler to logger
        finalize_logger.addHandler(file_handler)

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
    job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

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

    job.status = 'completed'
    job.save()

    reviewdir   = job.get_review_dir()
    shutil.rmtree(reviewdir)
   
    return redirect('job_detail', job_id=job_id)


@login_required
@require_http_methods(["POST"])
def retry_job(request, job_id):
    """Retry job with same settings"""
    # Allow admin users to retry any job, regular users can only retry their own jobs
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    job.status = 'processing'
    job.save()
    output_dir = job.get_output_dir()
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

        img_dir = os.path.join(output_dir, 'output')
        os.makedirs(img_dir, exist_ok=True)

        thumbnaildir = os.path.join(output_dir, 'thumbnails')
        os.makedirs(thumbnaildir, exist_ok=True)
    run_and_monitor_job(job) 
    
    return redirect('job_detail', job_id=job_id)


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
