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
from django.utils.safestring import mark_safe
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


max_deriving_jobs   = settings.MAX_DERIVING_JOBS
max_concurrent_jobs = settings.MAX_CONCURRENT_JOBS
check_interval      = settings.JOB_QUEUE_CHECK_INTERVAL

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
        jobs_list = ProcessingJob.objects.all().order_by('-created_at')
    else:
        jobs_list = ProcessingJob.objects.filter(user=request.user).order_by('-created_at')

    # Get status filter from query parameters
    status_filter = request.GET.get('status')
    if status_filter and status_filter in ['completed', 'processing', 'reviewing', 'failed', 'finalizing', 'preparing_review']:
        jobs_list = jobs_list.filter(status=status_filter)

    paginator = Paginator(jobs_list, 10)  # Show 10 jobs per page
    page_number = request.GET.get('page')
    jobs = paginator.get_page(page_number)

    # Get all jobs for statistics (not filtered by status, but filtered by user if not staff)
    if request.user.is_staff:
        all_jobs_list = ProcessingJob.objects.all()
    else:
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
        thread = threading.Thread(target=run_and_monitor_job, args=(job,))
        thread.start()

        logger.info(f"Started processing job {job.id} in background thread")
        messages.success(request, f'Job "{job.title or "Untitled"}" has been submitted and is being processed.')
        return redirect('job_detail', job_id=job.id)

    return render(request, 'repub_interface/home.html', {
        'form': form,
        'jobs': jobs
    })

def run_and_monitor_job(job):
    """
    Start job processing with concurrent job limiting.
    Waits if maximum concurrent jobs are already running.
    """

    # Wait until there are fewer than max concurrent jobs processing
    while True:
        processing_count = ProcessingJob.objects.filter(status='processing').count()

        if processing_count < max_concurrent_jobs:
            # Start processing in background thread
            logger.info(f"Starting job {job.id}. Current processing jobs: {processing_count}/{max_concurrent_jobs}")
            run_job(job)
            break
        else:
            # Wait before checking again
            logger.info(f"Job {job.id} waiting in queue. Current processing jobs: {processing_count}/{max_concurrent_jobs}")
            time.sleep(check_interval)

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

        self.maxcontours  = job.maxcontours
        self.xmax         = job.xmaximum
        self.ymax         = job.ymax
        self.mingray      = job.mingray
        self.crop         = job.crop
        self.deskew       = job.deskew
        self.do_ocr       = job.ocr

        if self.do_ocr:
            self.outhocr = os.path.join(output_dir, 'x_hocr.html.gz')
            self.outtxt  = os.path.join(output_dir, 'x_text.txt')
        else:
            self.outhocr = None     
            self.outtxt  = None

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
    thread = threading.Thread(target=run_and_monitor_job, args=(job,))
    thread.start()
    
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


def run_derive_job(job, derive_reduce_factor=None):
    """Background thread function to derive a job"""
    try:
        # Get metadata to extract the Identifier
        input_dir = job.get_input_dir()
        scandir = Scandir(input_dir, None, None, logger)
        metadata = scandir.metadata

        identifier = metadata.get('/Identifier')
        if not identifier:
            job.status = 'derive_failed'
            job.error_message = 'No identifier found in job metadata.'
            job.save()
            logger.error(f"No identifier found for job {job.id}")
            return

        # Create derive directory with the identifier name
        derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')
        os.makedirs(derive_base_dir, exist_ok=True)

        derive_dir = os.path.join(derive_base_dir, identifier)
        if os.path.exists(derive_dir):
            job.status = 'derive_failed'
            job.error_message = f'Derive directory "{identifier}" already exists.'
            job.save()
            logger.error(f"Derive directory already exists: {derive_dir}")
            return

        os.makedirs(derive_dir, exist_ok=True)
        logger.info(f"Created derive directory: {derive_dir}")

        # Copy input file
        if job.input_file and os.path.exists(job.input_file.path):
            input_filename = os.path.basename(job.input_file.path)
            input_dest = os.path.join(derive_dir, input_filename)
            shutil.copy2(job.input_file.path, input_dest)
            logger.info(f"Copied input file to: {input_dest}")

        # Zip output image folder
        output_img_dir = job.get_outimg_dir()
        if os.path.exists(output_img_dir):
            zip_filename = f'{identifier}_images.zip'
            zip_path = os.path.join(derive_dir, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(output_img_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, output_img_dir)
                        zipf.write(file_path, arcname)
            logger.info(f"Created output images zip: {zip_path}")

        # Zip thumbnail folder
        thumbnail_dir = job.get_thumbnail_dir()
        if os.path.exists(thumbnail_dir):
            zip_filename = f'{identifier}_thumbnails.zip'
            zip_path = os.path.join(derive_dir, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(thumbnail_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, thumbnail_dir)
                        zipf.write(file_path, arcname)
            logger.info(f"Created thumbnails zip: {zip_path}")

        # Copy thumbnail if it exists
        output_dir = job.get_output_dir()
        thumb_path = os.path.join(output_dir, '__ia_thumb.jpg')
        if os.path.exists(thumb_path):
            thumb_dest = os.path.join(derive_dir, '__ia_thumb.jpg')
            shutil.copy2(thumb_path, thumb_dest)
            logger.info(f"Copied thumbnail to: {thumb_dest}")

        # Check if HOCR file exists, if not regenerate PDF with OCR
        hocr_path = os.path.join(output_dir, 'x_hocr.html.gz')
        pdf_path = os.path.join(output_dir, 'x_final.pdf')
        text_path = os.path.join(output_dir, 'x_text.txt')

        if not os.path.exists(hocr_path) or not os.path.exists(pdf_path) or \
                not os.path.exists(text_path):
            logger.info(f"HOCR file not found for job {job.id}, regenerating PDF with OCR in derive directory")

            # Generate PDF with OCR directly (removed the queue logic for now)
            derive_pdf(job, identifier, metadata, derive_dir, derive_reduce_factor)
        else:
            # Copy existing PDF if it exists
            pdf_dest = os.path.join(derive_dir, f'{identifier}.pdf')
            shutil.copy2(pdf_path, pdf_dest)
            logger.info(f"Copied PDF to: {pdf_dest}")

            hocr_dest = os.path.join(derive_dir, f'{identifier}_hocr.html.gz')
            shutil.copy2(hocr_path, hocr_dest)
            logger.info(f"Copied HOCR to: {hocr_dest}")

            text_dest = os.path.join(derive_dir, f'{identifier}_text.txt')
            shutil.copy2(text_path, text_dest)
            logger.info(f"Copied text file to: {text_dest}")

            # Clean up output folder and related directories
            output_base_dir = job.get_output_dir()
            if os.path.exists(output_base_dir):
                shutil.rmtree(output_base_dir)
                logger.info(f"Cleaned up output directory: {output_base_dir}")

            # Clean up uploads folder
            upload_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
            if os.path.exists(upload_base_dir):
                shutil.rmtree(upload_base_dir)
                logger.info(f"Cleaned up upload directory: {upload_base_dir}")

        logger.info(f"Successfully derived job {job.id} to {derive_dir}")

        job.is_derived = True
        job.derived_identifier = identifier
        job.derived_at = timezone.now()
        job.status = 'derive_completed'
        job.save()
        logger.info(f"Updated job {job.id} with derived info: {identifier}")

    except Exception as e:
        job.status = 'derive_failed'
        job.error_message = str(e)
        job.save()
        logger.error(f"Error deriving job {job.id}: {str(e)}", exc_info=True)

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
    if job.status == 'deriving':
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

    # Set job status to deriving
    job.status = 'deriving'
    job.error_message = ''
    job.save()

    # Start derivation in background thread
    thread = threading.Thread(target=run_derive_job, args=(job, derive_reduce_factor))
    thread.start()

    logger.info(f"Started derivation for job {job.id} in background thread with reduce_factor={derive_reduce_factor}")
    messages.success(request, f'Job "{job.title or "Untitled"}" is being derived.')

    return redirect('job_detail', job_id=job_id)

def run_and_monitor_pdf(job, identifier, metadata, derive_dir):
    """
    Start job processing with concurrent job limiting.
    Waits if maximum concurrent jobs are already running.
    """

    # Wait until there are fewer than max concurrent jobs processing
    while True:
        processing_count = ProcessingJob.objects.filter(status='deriving').count()

        if processing_count < max_deriving_jobs:
            # Start processing in background thread
            logger.info(f"Starting job {job.id}. Current processing jobs: {processing_count}/{max_deriving_jobs}")
            derive_pdf(job, identifier, metadata, derive_dir) 
            break
        else:
            # Wait before checking again
            logger.info(f"Job {job.id} waiting in queue. Current processing jobs: {processing_count}/{max_deriving_jobs}")
            time.sleep(check_interval)

def derive_pdf(job, identifier, metadata, derive_dir, derive_reduce_factor=None):
    temp_dir = None
    try:
        # Prepare output file paths in derive directory
        pdf_dest = os.path.join(derive_dir, f'{identifier}.pdf')
        hocr_dest = os.path.join(derive_dir, f'{identifier}_hocr.html.gz')
        text_dest = os.path.join(derive_dir, f'{identifier}_text.txt')

        # Get list of output images
        output_img_dir = job.get_outimg_dir()
        outfiles = []
        for filename in sorted(os.listdir(output_img_dir)):
            outfile = os.path.join(output_img_dir, filename)
            pagenum = get_pagenum(filename)
            if pagenum is not None:
                outfiles.append((pagenum, outfile))

        outfiles.sort(key=lambda x: x[0])

        # Create a logger for the OCR process
        derive_logger = logging.getLogger(f'repub.derive.{job.id}')
        derive_logger.info(f"Regenerating PDF with OCR for {len(outfiles)} pages")

        # If reduce_factor is specified, create reduced images
        if derive_reduce_factor is not None:
            import tempfile
            temp_dir = tempfile.mkdtemp(prefix=f'derive_reduced_{job.id}_')
            derive_logger.info(f"Reducing images by factor {derive_reduce_factor} in temporary directory: {temp_dir}")

            reduced_outfiles = []
            for pagenum, outfile in outfiles:
                # Read the image
                img = cv2.imread(outfile)
                if img is None:
                    derive_logger.warning(f"Failed to read image {outfile}, using original")
                    reduced_outfiles.append((pagenum, outfile))
                    continue

                # Calculate new dimensions
                height, width = img.shape[:2]
                new_width = int(width * derive_reduce_factor)
                new_height = int(height * derive_reduce_factor)

                # Resize the image
                reduced_img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)

                # Save reduced image to temp directory
                filename = os.path.basename(outfile)
                reduced_path = os.path.join(temp_dir, filename)
                cv2.imwrite(reduced_path, reduced_img)

                reduced_outfiles.append((pagenum, reduced_path))
                derive_logger.info(f"Reduced page {pagenum} from {width}x{height} to {new_width}x{new_height}")

            # Use reduced images for PDF generation
            outfiles = reduced_outfiles
            derive_logger.info(f"Using {len(outfiles)} reduced images for PDF generation")

        # Generate PDF with OCR, HOCR, and text
        pdfs.save_pdf(outfiles, metadata, job.language, pdf_dest,
                      True, hocr_dest, text_dest, derive_logger)

        logger.info(f"Generated PDF with OCR in derive directory: {pdf_dest}")
        logger.info(f"Generated HOCR in derive directory: {hocr_dest}")
        logger.info(f"Generated text in derive directory: {text_dest}")

        # Clean up temporary directory if it was created
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"Cleaned up temporary directory: {temp_dir}")

        # Clean up output folder and related directories after successful PDF generation
        output_base_dir = job.get_output_dir()
        if os.path.exists(output_base_dir):
            shutil.rmtree(output_base_dir)
            logger.info(f"Cleaned up output directory: {output_base_dir}")

        # Clean up uploads folder
        upload_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
        if os.path.exists(upload_base_dir):
            shutil.rmtree(upload_base_dir)
            logger.info(f"Cleaned up upload directory: {upload_base_dir}")

        # Update job status to completed
        job.status = 'derive_completed'
        job.save()
        logger.info(f"Updated job {job.id} to derive_completed after PDF generation")

    except Exception as e:
        logger.error(f"Error generating PDF for job {job.id}: {str(e)}", exc_info=True)

        # Clean up temporary directory if it exists
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up temporary directory: {str(cleanup_error)}")

        job.status = 'derive_failed'
        job.save()
        logger.error(f"Updated job {job.id} to derive_failed")

@login_required
def all_items(request):
    """View all derived items"""
    derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')

    # Get derived jobs - staff users see all, regular users see only their own
    if request.user.is_staff:
        derived_jobs = ProcessingJob.objects.filter(is_derived=True)
    else:
        derived_jobs = ProcessingJob.objects.filter(is_derived=True, user=request.user)

    # Create a mapping of identifier to job owner
    identifier_to_owner = {}
    for job in derived_jobs:
        if job.derived_identifier:
            identifier_to_owner[job.derived_identifier] = job.user

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
                if not request.user.is_staff and identifier not in identifier_to_owner:
                    continue

                item_info = {
                    'identifier': identifier,
                    'path': item_path,
                    'owner': identifier_to_owner.get(identifier),  # Add owner information
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

    context = {
        'items': items,
        'total_items': len(items),
    }

    return render(request, 'repub_interface/all_items.html', context)


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
