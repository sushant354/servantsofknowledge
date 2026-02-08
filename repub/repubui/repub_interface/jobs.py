import os
import shutil
import threading
import cv2
import logging
import csv

from PIL import Image
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.db import models
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.utils.safestring import mark_safe
from .models import ProcessingJob
from .forms import ProcessingJobForm, ProcessingOptionsForm
from .tasks import derive_job_task


from repub.utils.scandir import Scandir, get_pagenum
from repub.utils import pdfs
from .tasks import Args
from .users import login_or_token_required

# Set up logger for this module
logger = logging.getLogger('repubui.views')

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
    allowed_sort_fields = ['created_at', 'processing_started_at', 'derive_started']
    if sort_by not in allowed_sort_fields:
        sort_by = 'created_at'

    # Apply sorting - nullable fields need special handling for nulls_last
    nullable_sort_fields = ['processing_started_at', 'derive_started']
    if sort_order == 'asc':
        if sort_by in nullable_sort_fields:
            jobs_list = jobs_list.order_by(models.F(sort_by).asc(nulls_last=True))
        else:
            jobs_list = jobs_list.order_by(sort_by)
    else:
        if sort_by in nullable_sort_fields:
            jobs_list = jobs_list.order_by(models.F(sort_by).desc(nulls_last=True))
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
@csrf_exempt
@login_or_token_required
def check_identifier(request):
    """
    API endpoint to check if an identifier already exists.
    Checks both ProcessingJob records and derived item directories.
    """
    identifier = request.GET.get('identifier', '').strip()
    if not identifier:
        return JsonResponse({'success': False, 'error': 'identifier parameter is required'}, status=400)

    # Check existing jobs
    existing_job = ProcessingJob.objects.filter(identifier=identifier).first()
    if existing_job:
        return JsonResponse({
            'success': True,
            'exists': True,
            'identifier': identifier,
            'detail': f'Identifier is already used by job {existing_job.id}'
        })

    # Check derived directory
    derive_dir = os.path.join(settings.MEDIA_ROOT, 'derived', identifier)
    if os.path.exists(derive_dir):
        return JsonResponse({
            'success': True,
            'exists': True,
            'identifier': identifier,
            'detail': 'Identifier already exists as a derived item'
        })

    return JsonResponse({'success': True, 'exists': False, 'identifier': identifier})


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
