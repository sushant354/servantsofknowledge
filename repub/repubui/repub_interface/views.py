import os
import zipfile
import shutil
import threading
import cv2
import json
import time
import logging
import mimetypes
from pathlib import Path
from PIL import Image
from io import BytesIO
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from .models import ProcessingJob, PageImage
from .forms import ProcessingJobForm, UserRegistrationForm
import numpy as np

# Set up logger for this module
logger = logging.getLogger(__name__)

# Import functions from the original repub package
from repub import process_raw 
from repub.imgfuncs.cropping import crop
from repub.utils.scandir import Scandir
from repub.utils import pdfs

@login_required
def all_jobs(request):
    jobs_list = ProcessingJob.objects.filter(user=request.user).order_by('-created_at')
    
    paginator = Paginator(jobs_list, 10)  # Show 10 jobs per page
    page_number = request.GET.get('page')
    jobs = paginator.get_page(page_number)
    
    context = {
        'jobs': jobs,
        'total_jobs': jobs_list.count(),
        'completed_jobs': jobs_list.filter(status='completed').count(),
        'processing_jobs': jobs_list.filter(status='processing').count(),
        'failed_jobs': jobs_list.filter(status='failed').count(),
        'reviewing_jobs': jobs_list.filter(status='reviewing').count(),
    }
    
    return render(request, 'repub_interface/all_jobs.html', context)


@login_required
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

        logger.debug(f"Created job {job.id} with file: {job.input_file}")

        # Start processing in background thread
        thread = threading.Thread(target=process_job, args=(job.id,))
        thread.daemon = True
        thread.start()

        logger.debug(f"Started processing job {job.id} in background thread")
        messages.success(request, f'Job "{job.title or "Untitled"}" has been submitted and is being processed.')
        return redirect('job_detail', job_id=job.id)

    return render(request, 'repub_interface/home.html', {
        'form': form,
        'jobs': jobs
    })


@login_required
def job_detail(request, job_id):
    job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    
    # If the job is in reviewing status, redirect to the review page
    if job.status == 'reviewing':
        return redirect('job_review', job_id=job.id)
        
    return render(request, 'repub_interface/job_detail.html', {
        'job': job
    })


@login_required
def job_review(request, job_id):
    job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
    pages = job.pages.all().order_by('page_number')
    
    if request.method == 'POST' and 'finalize' in request.POST:
        # Start finalizing in background thread
        job.status = 'finalizing'
        job.save()
        thread = threading.Thread(target=finalize_job, args=(job.id,))
        thread.daemon = True
        thread.start()
        return redirect('job_detail', job_id=job.id)
    
    # Add current timestamp for cache busting
    current_time = {'timestamp': int(time.time())}
    
    return render(request, 'repub_interface/job_review.html', {
        'job': job,
        'pages': pages,
        'now': current_time,
        'media_url': settings.MEDIA_URL
    })


@require_http_methods(["GET"])
@login_required
def page_editor(request, job_id, page_number):
    """
    View for editing page crops with optimized loading and error handling.
    """
    try:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
        page = get_object_or_404(PageImage, job=job, page_number=page_number)
        
        # Get adjacent pages for navigation
        next_page = PageImage.objects.filter(
            job=job, 
            page_number__gt=page_number
        ).order_by('page_number').first()
        
        prev_page = PageImage.objects.filter(
            job=job, 
            page_number__lt=page_number
        ).order_by('-page_number').first()
        
        # Validate crop box data
        if page.user_crop_box:
            try:
                json.loads(page.user_crop_box)
            except json.JSONDecodeError:
                page.user_crop_box = None
                page.save()
        
        context = {
            'job': job,
            'page': page,
            'next_page': next_page,
            'prev_page': prev_page,
            'media_url': settings.MEDIA_URL,
            'now': timezone.now()
        }
        
        return render(request, 'repub_interface/page_editor.html', context)
        
    except Exception as e:
        logger.error(f"Error in page_editor view: {str(e)}", exc_info=True)
        messages.error(request, "An error occurred while loading the page editor.")
        return redirect('job_detail', job_id=job_id)


@require_http_methods(["POST"])
@csrf_exempt
@login_required
def update_crop(request, job_id, page_number):
    """
    API endpoint for updating page crops with optimized processing and error handling.
    """
    try:
        # Validate input data
        try:
            crop_data = json.loads(request.body)
            required_fields = ['x1', 'y1', 'x2', 'y2']
            if not all(field in crop_data for field in required_fields):
                raise ValueError("Missing required crop coordinates")
            
            # Validate coordinate types
            if not all(isinstance(crop_data[field], (int, float)) for field in required_fields):
                raise ValueError("Invalid coordinate types")
            
        except (json.JSONDecodeError, ValueError) as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Invalid crop data: {str(e)}'
            }, status=400)
        
        # Get job and page objects
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
        page = get_object_or_404(PageImage, job=job, page_number=page_number)
        
        # Validate original image
        original_image_path = os.path.join(settings.MEDIA_ROOT, page.original_image)
        if not os.path.exists(original_image_path):
            return JsonResponse({
                'status': 'error',
                'message': 'Original image file not found'
            }, status=404)
        
        # Load and validate image
        img = cv2.imread(original_image_path)
        if img is None or img.size == 0:
            return JsonResponse({
                'status': 'error',
                'message': 'Failed to load image (empty or invalid format)'
            }, status=400)
        
        # Get image dimensions
        h, w = img.shape[:2]
        
        # Validate and fix crop coordinates
        crop_box = [
            max(0, min(float(crop_data['x1']), w-1)),  # x1
            max(0, min(float(crop_data['y1']), h-1)),  # y1
            max(1, min(float(crop_data['x2']), w)),    # x2
            max(1, min(float(crop_data['y2']), h)),    # y2
            None  # No rotation angle
        ]
        
        # Ensure minimum crop size
        if crop_box[2] - crop_box[0] < 10 or crop_box[3] - crop_box[1] < 10:
            return JsonResponse({
                'status': 'error',
                'message': 'Crop area too small (minimum 10x10 pixels)'
            }, status=400)
        
        # Update page with crop box
        page.set_user_crop_box(crop_box)
        page.reviewed = True
        page.needs_review = False
        
        # Apply crop
        try:
            cropped_img = crop(img, crop_box)
            if cropped_img is None or cropped_img.size == 0:
                raise ValueError("Cropping operation failed")
            
            # Prepare output paths
            output_filename = f"{page.page_number:04d}_adjusted.jpg"
            relative_path = os.path.join(job.get_output_dir(), output_filename)
            output_path = os.path.join(settings.MEDIA_ROOT, relative_path)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Save cropped image with quality optimization
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, 95]
            if not cv2.imwrite(output_path, cropped_img, encode_params):
                raise IOError("Failed to save cropped image")
            
            # Update page with new image paths
            page.adjusted_image = relative_path
            page.cropped_image = relative_path
            
            # Generate and save thumbnail
            if page.cropped_thumbnail:
                page.cropped_thumbnail.delete(save=False)
            
            thumbnail = create_thumbnail(output_path)
            if thumbnail:
                thumbnail_filename = f"{page.page_number:04d}_cropped_thumb.jpg"
                thumbnail_io = BytesIO()
                thumbnail.save(thumbnail_io, format='JPEG', quality=85)
                
                thumbnail_file = ContentFile(thumbnail_io.getvalue())
                page.cropped_thumbnail.save(
                    thumbnail_filename,
                    InMemoryUploadedFile(
                        thumbnail_file,
                        None,
                        thumbnail_filename,
                        'image/jpeg',
                        len(thumbnail_io.getvalue()),
                        None
                    )
                )
            
            # Save all changes
            page.save()
            
            # Create cache buster
            timestamp = int(time.time())
            
            return JsonResponse({
                'status': 'success',
                'message': 'Crop updated successfully',
                'adjusted_url': f"{settings.MEDIA_URL}{relative_path}?t={timestamp}",
                'thumbnail_url': f"{page.cropped_thumbnail.url}?t={timestamp}" if page.cropped_thumbnail else None
            })
            
        except Exception as e:
            logger.error(f"Error processing crop: {str(e)}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': f'Error processing image: {str(e)}'
            }, status=500)
            
    except Exception as e:
        logger.error(f"Unhandled exception in update_crop: {str(e)}", exc_info=True)
        return JsonResponse({
            'status': 'error',
            'message': 'An unexpected error occurred'
        }, status=500)


@login_required
def job_download(request, job_id):
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
        self.crop         = job.crop
        self.deskew       = job.deskew
        self.do_ocr       = job.ocr

        self.dewarp       = job.dewarp
        self.drawcontours = job.draw_contours
        self.gray         = job.gray
        self.rotate_type  = job.rotate_type
        self.factor       = job.reduce_factor
        self.pagenums     = None

def process_job(job_id):
    job = ProcessingJob.objects.get(id=job_id)
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

    loghandle.write(f"Processing job ID: {job.id}\n")
    loghandle.write(f"Input file: {input_file_path}\n")
    loghandle.write(f"Input file exists: {os.path.exists(input_file_path) if input_file_path else False}\n")
    loghandle.write(f"Input directory: {input_dir}\n")

    # Process based on input type
    if job.input_type == 'pdf':
        loghandle.write("Input type: PDF")
        if not input_file_path or not os.path.exists(input_file_path):
            raise ValueError(f"PDF file not found at: {input_file_path}")
        # Extract images from PDF
        pdfs.pdf_to_images(input_file_path, input_dir)
    elif job.input_type == 'images':
        loghandle.write("Input type: Images\n")
        if not input_file_path or not os.path.exists(input_file_path):
            raise ValueError(f"Image file not found at: {input_file_path}\n")
        # If it's a ZIP file, extract it
        if input_file_path.lower().endswith('.zip'):
            loghandle.write("Extracting ZIP file\n")
            with zipfile.ZipFile(input_file_path, 'r') as zip_ref:
                zip_ref.extractall(input_dir)

            # List all extracted files for debugging
            n = 0 
            for root, dirs, files in os.walk(input_dir):
                for file in files:
                    n += 1
            loghandle.write(f"Extracted {n} files from ZIP\n")

            # If it's a single image, copy it to the input directory
        elif input_file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
            loghandle.write("Processing single image file")
            filename = os.path.basename(input_file_path)
            destination = os.path.join(input_dir, filename)
            shutil.copy(input_file_path, destination)

        args = Args(job, input_dir, output_dir)
        scandir = Scandir(args.indir, args.outdir, args.pagenums)
        if job.input_type == 'pdf':
            metadata = pdfs.get_metadata(input_file_path)
        else:
            metadata = scandir.metadata
        if args.drawcontours:
            outfiles = process_raw.draw_contours(scandir, args)
        elif args.gray:
            outfiles = process_raw.gray_images(scandir, args)
        elif args.deskew and not args.crop:
            outfiles = process_raw.deskew_images(scandir, args)
        else:
            outfiles = process_raw.process_images(scandir, args)
            if args.outpdf:
                pdfs.save_pdf(outfiles, metadata, args.langs, args.outpdf, \
                              args.do_ocr, args.outhocr, args.outtxt)
                relative_path = os.path.relpath(args.outpdf, settings.MEDIA_ROOT)
                job.output_file = relative_path
        

        process_img_files(scandir, job)

        job.status = 'completed'
        job.save()


def process_img_files(scandir, job):        
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        # Save original file reference
        rel_original_path = os.path.relpath(infile, settings.MEDIA_ROOT)
            
        # Create PageImage object
        page = PageImage(job=job, page_number=pagenum, \
                         original_image=rel_original_path)
            
        # Create thumbnail for original image
        original_thumbnail = create_thumbnail(infile)
            
        if original_thumbnail:
            # Save the thumbnail
            fname = f"{pagenum:04d}_original_thumb.jpg"
            thumbnail_io = BytesIO()
            original_thumbnail.save(thumbnail_io, format='JPEG')
                
            # Create and save thumbnail
            thumbnail_file = ContentFile(thumbnail_io.getvalue())
            page.original_thumbnail.save(fname, InMemoryUploadedFile(
                    thumbnail_file,
                    None,
                    fname,
                    'image/jpeg',
                    len(thumbnail_io.getvalue()),
                    None
                ))
       
        # Create thumbnail for final image
        final_thumbnail = create_thumbnail(outfile)
                
        if final_thumbnail:
            # Save the thumbnail
            fname = f"{pagenum:04d}_cropped_thumb.jpg"
            thumbnail_io = BytesIO()
            final_thumbnail.save(thumbnail_io, format='JPEG')
                    
            # Create and save thumbnail
            thumbnail_file = ContentFile(thumbnail_io.getvalue())
            page.cropped_thumbnail.save(fname, InMemoryUploadedFile(
                        thumbnail_file,
                        None,
                        fname,
                        'image/jpeg',
                        len(thumbnail_io.getvalue()),
                        None
                    ))
                
        # Save the page object
        page.save()

@require_http_methods(["POST"])
@csrf_exempt
@login_required
def save_snip(request, job_id, page_number):
    """
    API endpoint for saving snipped images.
    """
    try:
        # Get job and page objects
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
        page = get_object_or_404(PageImage, job=job, page_number=page_number)
        
        # Validate file upload
        if 'snipped_image' not in request.FILES:
            return JsonResponse({
                'status': 'error',
                'message': 'No image file provided'
            }, status=400)
        
        snipped_image = request.FILES['snipped_image']
        
        try:
            # Convert uploaded image to OpenCV format
            image_data = snipped_image.read()
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None or img.size == 0:
                raise ValueError("Invalid image data")
            
            # Prepare output paths
            output_filename = f"{page.page_number:04d}_adjusted.jpg"
            relative_path = os.path.join(job.get_output_dir(), output_filename)
            output_path = os.path.join(settings.MEDIA_ROOT, relative_path)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Save snipped image with quality optimization
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, 95]
            if not cv2.imwrite(output_path, img, encode_params):
                raise IOError("Failed to save snipped image")
            
            # Update page with new image paths
            page.adjusted_image = relative_path
            page.cropped_image = relative_path
            
            # Generate and save thumbnail
            if page.cropped_thumbnail:
                page.cropped_thumbnail.delete(save=False)
            
            thumbnail = create_thumbnail(output_path)
            if thumbnail:
                thumbnail_filename = f"{page.page_number:04d}_cropped_thumb.jpg"
                thumbnail_io = BytesIO()
                thumbnail.save(thumbnail_io, format='JPEG', quality=85)
                
                thumbnail_file = ContentFile(thumbnail_io.getvalue())
                page.cropped_thumbnail.save(
                    thumbnail_filename,
                    InMemoryUploadedFile(
                        thumbnail_file,
                        None,
                        thumbnail_filename,
                        'image/jpeg',
                        len(thumbnail_io.getvalue()),
                        None
                    )
                )
            
            # Mark page as reviewed
            page.reviewed = True
            page.needs_review = False
            page.save()
            
            # Create cache buster
            timestamp = int(time.time())
            
            return JsonResponse({
                'status': 'success',
                'message': 'Snip saved successfully',
                'adjusted_url': f"{settings.MEDIA_URL}{relative_path}?t={timestamp}",
                'thumbnail_url': f"{page.cropped_thumbnail.url}?t={timestamp}" if page.cropped_thumbnail else None
            })
            
        except Exception as e:
            logger.error(f"Error processing snipped image: {str(e)}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': f'Error processing image: {str(e)}'
            }, status=500)
            
    except Exception as e:
        logger.error(f"Unhandled exception in save_snip: {str(e)}", exc_info=True)
        return JsonResponse({
            'status': 'error',
            'message': 'An unexpected error occurred'
        }, status=500)


@require_http_methods(["GET"])
@login_required
def job_status(request, job_id):
    """
    API endpoint for checking job status via AJAX.
    """
    try:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)
        return JsonResponse({
            'status': job.status,
            'needs_review': job.needs_review
        })
    except Exception as e:
        return JsonResponse({
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
                try:
                    stat_info = os.stat(item_path)
                    item_info['size'] = format_file_size(stat_info.st_size)
                    item_info['modified'] = timezone.datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.get_current_timezone())
                        
                    # Get mime type
                    mime_type, _ = mimetypes.guess_type(item_path)
                    item_info['mime_type'] = mime_type
                      
                    # Create relative URL for media files
                    relative_path = os.path.relpath(item_path, settings.MEDIA_ROOT)
                    item_info['relative_url'] = f"{settings.MEDIA_URL}{relative_path}"
                        
                except Exception as e:
                    logger.error(f"Error getting file info for {item_path}: {e}")
                
            items.append(item_info)
    except PermissionError:
        messages.error(request, 'Permission denied accessing directory.')
        return redirect('job_output_directory', job_id=job_id)
    except Exception as e:
        logger.error(f"Error reading directory {current_dir}: {e}")
        messages.error(request, 'Error reading directory contents.')
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
            user = form.save()
            login(request, user)
            messages.success(request, 'Registration successful!')
            return redirect('home')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'registration/register.html', {'form': form})
