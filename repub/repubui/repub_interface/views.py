import os
import tempfile
import zipfile
import shutil
import threading
import cv2
import json
import time
import logging
from PIL import Image
from io import BytesIO
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ProcessingJob, PageImage
from .forms import ProcessingJobForm, UserRegistrationForm
import numpy as np

# Set up logger for this module
logger = logging.getLogger(__name__)

# Import functions from the original repub package
from repub.process_raw import setup_logging
from repub.imgfuncs.cropping import get_crop_box, crop, fix_wrong_boxes
from repub.imgfuncs.deskew import rotate, deskew
from repub.utils.pdfs import pdf_to_images, save_pdf
from repub.imgfuncs.utils import find_contour, threshold_gray


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

        # Create directories for this job
        input_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
        output_dir = os.path.join(settings.MEDIA_ROOT, 'processed', str(job.id))
        thumbnail_dir = os.path.join(settings.MEDIA_ROOT, 'thumbnails', str(job.id))
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(thumbnail_dir, exist_ok=True)

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
@csrf_protect
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


def resize_image(img, factor):
    """Resize an image based on a factor"""
    (h, w) = img.shape[:2]
    width = int(w * factor)
    height = int(h * factor)
    dim = (width, height)
    return cv2.resize(img, dim, interpolation=cv2.INTER_AREA)


def get_scanned_pages(input_dir):
    """Get all image files from the input directory and its subdirectories"""
    import re
    import os

    # Find all image files recursively
    image_files = []
    seen_page_numbers = set()  # To track page numbers we've already seen

    # First pass: collect all full-size images (not in thumbnails directory)
    for root, dirs, files in os.walk(input_dir):
        # Skip thumbnails directory
        if 'thumbnails' in root.lower():
            continue

        for filename in files:
            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
                # Skip files that don't look like normal page scans
                if 'slip' in filename.lower() or 'metadata' in filename.lower():
                    continue

                full_path = os.path.join(root, filename)

                # Try to get page number from filename
                reobj = re.match(r'.*?(\d+)\.(jpg|jpeg|png|tif|tiff)$', filename.lower())
                if reobj:
                    pagenum = int(reobj.group(1))
                    if pagenum in seen_page_numbers:
                        continue  # Skip duplicates
                    seen_page_numbers.add(pagenum)
                else:
                    # If no page number in filename, use a counter
                    pagenum = len(image_files) + 1

                image_files.append((full_path, pagenum))

    # Sort by page number
    image_files.sort(key=lambda x: x[1])

    # Process each file
    for full_path, pagenum in image_files:
        try:
            img = cv2.imread(full_path)
            if img is not None and img.size > 0:
                yield (img, full_path, pagenum)
            else:
                logger.warning(f"Could not read image or empty image: {full_path}")
        except Exception as e:
            logger.error(f"Error reading image {full_path}: {str(e)}", exc_info=True)
            continue


def get_cropping_boxes(input_dir, args):
    """Create cropping boxes for all pages"""
    boxes = {}

    # Process each page to get cropping boxes
    try:
        for img, outfile, pagenum in get_scanned_pages(input_dir):
            # Skip processing if image is None or empty
            if img is None or img.size == 0:
                logger.warning(f"Skipping empty image for cropping: {outfile}")
                continue

            try:
                if args['deskew']:
                    img, hangle = deskew(img, args['xmax'], args['ymax'], args['maxcontours'], args['rotate_type'])
                else:
                    hangle = None

                box = get_crop_box(img, args['xmax'], args['ymax'], args['maxcontours'])
                box.append(hangle)
                boxes[pagenum] = box
            except Exception as e:
                logger.error(f"Error creating crop box for page {pagenum}: {str(e)}", exc_info=True)
                continue
    except Exception as e:
        logger.error(f"Error in get_cropping_boxes: {str(e)}", exc_info=True)
        return boxes

    # Only fix boxes if we have enough pages
    if len(boxes) >= 2:
        try:
            fix_wrong_boxes(boxes, 200, 250)
        except (TypeError, ValueError) as e:
            # Log the error but continue with the existing boxes
            logger.warning(f"Error fixing cropping boxes: {str(e)}")

    return boxes


def process_job(job_id):
    job = ProcessingJob.objects.get(id=job_id)
    job.status = 'processing'
    job.save()

    try:
        # Set up logging
        setup_logging('info')

        # Get the input and output directories/files
        input_file_path = job.input_file.path if job.input_file else None
        input_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(job.id))
        output_dir = os.path.join(settings.MEDIA_ROOT, 'processed', str(job.id))
        thumbnail_dir = os.path.join(settings.MEDIA_ROOT, 'thumbnails', str(job.id))
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(thumbnail_dir, exist_ok=True)

        debug_info = []
        debug_info.append(f"Processing job ID: {job.id}")
        debug_info.append(f"Input file: {input_file_path}")
        debug_info.append(f"Input file exists: {os.path.exists(input_file_path) if input_file_path else False}")
        debug_info.append(f"Input directory: {input_dir}")

        # Process based on input type
        if job.input_type == 'pdf':
            debug_info.append("Input type: PDF")
            if not input_file_path or not os.path.exists(input_file_path):
                raise ValueError(f"PDF file not found at: {input_file_path}")
            # Extract images from PDF
            pdf_to_images(input_file_path, input_dir)
        elif job.input_type == 'images':
            debug_info.append("Input type: Images")
            if not input_file_path or not os.path.exists(input_file_path):
                raise ValueError(f"Image file not found at: {input_file_path}")
            # If it's a ZIP file, extract it
            if input_file_path.lower().endswith('.zip'):
                debug_info.append("Extracting ZIP file")
                with zipfile.ZipFile(input_file_path, 'r') as zip_ref:
                    zip_ref.extractall(input_dir)

                # List all extracted files for debugging
                all_files = []
                for root, dirs, files in os.walk(input_dir):
                    for file in files:
                        all_files.append(os.path.join(root, file))
                debug_info.append(f"Extracted {len(all_files)} files from ZIP")
                if all_files:
                    debug_info.append(f"Extracted files: {all_files[:20]}")  # List first 20 files

            # If it's a single image, copy it to the input directory
            elif input_file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
                debug_info.append("Processing single image file")
                filename = os.path.basename(input_file_path)
                destination = os.path.join(input_dir, filename)
                shutil.copy(input_file_path, destination)

        # Create a dictionary of arguments similar to argparse
        args = {
            'indir': input_dir,
            'outdir': output_dir,
            'outpdf': os.path.join(output_dir, f"{job.title or 'processed'}.pdf"),
            'langs': job.language,
            'maxcontours': job.maxcontours,
            'xmax': job.xmaximum,
            'ymax': job.ymax,
            'crop': job.crop,
            'deskew': job.deskew,
            'do_ocr': job.ocr,
            'dewarp': job.dewarp,
            'draw_contours': job.draw_contours,
            'gray': job.gray,
            'rotate_type': job.rotate_type,
            'factor': job.reduce_factor,
            'pagenums': None,  # Process all pages
        }

        # Check if we have any files to process by enumerating all files recursively
        image_files = []
        for root, _, files in os.walk(input_dir):
            # Skip thumbnails directory
            if 'thumbnails' in root.lower():
                continue

            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
                    # Skip files that don't look like normal page scans
                    if 'slip' in file.lower() or 'metadata' in file.lower():
                        continue
                    image_files.append(os.path.join(root, file))

        debug_info.append(f"Found {len(image_files)} image files to process")
        if image_files:
            debug_info.append(f"First few image files: {image_files[:5]}")

        if not image_files:
            raise ValueError(
                f"No image files found to process. Please check your input file.\n\nDebug info:\n" + "\n".join(
                    debug_info))

        # Handle special processing modes: draw contours and grayscale
        if args['draw_contours']:
            debug_info.append("Processing in 'draw contours' mode")
            # Process each page to draw contours
            outfiles = []
            for img, outfile, pagenum in get_scanned_pages(input_dir):
                if args['deskew']:
                    img, hangle = deskew(img, args['xmax'], args['ymax'], args['maxcontours'], args['rotate_type'])
                
                contours = find_contour(img)
                contours = contours[:args['maxcontours']]
                img = cv2.drawContours(img, contours, -1, (0, 255, 0), 3)
                
                # Save processed image
                processed_file = os.path.join(output_dir, f"{pagenum:04d}.jpg")
                cv2.imwrite(processed_file, img)
                outfiles.append((pagenum, processed_file))
            
            # Sort outfiles by page number
            outfiles.sort(key=lambda x: x[0])
            
            # Save PDF
            metadata = None
            output_pdf_path = args['outpdf']
            save_pdf(outfiles, metadata, args['langs'], output_pdf_path, args['do_ocr'])
            
            # Update job with output file
            relative_path = os.path.relpath(output_pdf_path, settings.MEDIA_ROOT)
            job.output_file = relative_path
            job.status = 'completed'
            job.save()
            return
        
        elif args['gray']:
            debug_info.append("Processing in 'grayscale' mode")
            # Process each page to convert to grayscale
            outfiles = []
            for img, outfile, pagenum in get_scanned_pages(input_dir):
                if args['deskew']:
                    img, hangle = deskew(img, args['xmax'], args['ymax'], args['maxcontours'], args['rotate_type'])
                
                gray = threshold_gray(img, 125, 255)
                
                # Save processed image
                processed_file = os.path.join(output_dir, f"{pagenum:04d}.jpg")
                cv2.imwrite(processed_file, gray)
                outfiles.append((pagenum, processed_file))
            
            # Sort outfiles by page number
            outfiles.sort(key=lambda x: x[0])
            
            # Save PDF
            metadata = None
            output_pdf_path = args['outpdf']
            save_pdf(outfiles, metadata, args['langs'], output_pdf_path, args['do_ocr'])
            
            # Update job with output file
            relative_path = os.path.relpath(output_pdf_path, settings.MEDIA_ROOT)
            job.output_file = relative_path
            job.status = 'completed'
            job.save()
            return

        # Create cropping boxes if needed
        boxes = None
        if args['crop']:
            try:
                boxes = get_cropping_boxes(input_dir, args)
                if not boxes:
                    debug_info.append("Warning: Could not create cropping boxes")
            except Exception as e:
                debug_info.append(f"Warning: Error creating cropping boxes: {str(e)}")

        # Process each page and store thumbnails
        outfiles = []
        page_count = 0
        page_objects = []

        # Get all pages to process
        try:
            all_pages = list(get_scanned_pages(input_dir))
            debug_info.append(f"Retrieved {len(all_pages)} pages to process")
        except Exception as e:
            debug_info.append(f"Error retrieving pages: {str(e)}")
            all_pages = []

        for img, outfile, pagenum in all_pages:
            page_count += 1
            debug_info.append(f"Processing page {pagenum} ({outfile})")

            # Skip processing if image is None or empty
            if img is None or img.size == 0:
                debug_info.append(f"Skipping empty image: {outfile}")
                continue

            # Save original file reference
            rel_original_path = os.path.relpath(outfile, settings.MEDIA_ROOT)
            
            # Create PageImage object
            page = PageImage(
                job=job,
                page_number=pagenum,
                original_image=rel_original_path
            )
            
            # Create thumbnail for original image
            original_thumbnail = create_thumbnail(outfile)
            
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

            # Process the image
            processed_img = img.copy()
            
            if args['deskew'] and not args['crop']:  # If crop is True, deskew is already done in get_cropping_boxes
                try:
                    processed_img, angle = deskew(processed_img, args['xmax'], args['ymax'], args['maxcontours'], args['rotate_type'])
                    debug_info.append(f"Deskewed page {pagenum} with angle {angle}")
                except Exception as e:
                    debug_info.append(f"Warning: Error during deskew for page {pagenum}: {str(e)}")

            if args['crop'] and boxes and pagenum in boxes:
                try:
                    box = boxes[pagenum]
                    page.set_auto_crop_box(box)
                    
                    if box[4] is not None:
                        processed_img = rotate(processed_img, box[4])
                    processed_img = crop(processed_img, box)
                    debug_info.append(f"Cropped page {pagenum} with box {box}")
                    
                    # Mark page as needing review
                    page.needs_review = True
                except Exception as e:
                    debug_info.append(f"Warning: Error during cropping for page {pagenum}: {str(e)}")

            if args['factor'] and args['factor'] > 0 and args['factor'] != 1.0:
                try:
                    processed_img = resize_image(processed_img, args['factor'])
                    debug_info.append(f"Resized page {pagenum} with factor {args['factor']}")
                except Exception as e:
                    debug_info.append(f"Warning: Error during resize for page {pagenum}: {str(e)}")

            # Verify that img is not None or empty before saving
            if processed_img is None or processed_img.size == 0:
                debug_info.append(f"Skipping writing empty processed image for page {pagenum}")
                continue

            # Save processed image
            try:
                processed_file = os.path.join(output_dir, f"{pagenum:04d}.jpg")
                cv2.imwrite(processed_file, processed_img)
                outfiles.append((pagenum, processed_file))
                
                # Update page with cropped image path
                rel_cropped_path = os.path.relpath(processed_file, settings.MEDIA_ROOT)
                page.cropped_image = rel_cropped_path
                
                # Create thumbnail for cropped image
                cropped_thumbnail = create_thumbnail(processed_file)
                
                if cropped_thumbnail:
                    # Save the thumbnail
                    fname = f"{pagenum:04d}_cropped_thumb.jpg"
                    thumbnail_io = BytesIO()
                    cropped_thumbnail.save(thumbnail_io, format='JPEG')
                    
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
                page_objects.append(page)
                
            except Exception as e:
                debug_info.append(f"Error saving processed image for page {pagenum}: {str(e)}")

        debug_info.append(f"Processed {page_count} pages")

        if not outfiles:
            raise ValueError(
                f"No pages were successfully processed. Please check your input files.\n\nDebug info:\n" + "\n".join(
                    debug_info))

        # Check if any pages need review
        needs_review = any(page.needs_review for page in page_objects)
        
        if needs_review:
            # Set job status to awaiting review
            job.needs_review = True
            job.status = 'reviewing'
            job.save()
            debug_info.append("Job marked as awaiting review")
            return
            
        # If no review needed, proceed directly to finalization
        finalize_job(job.id)

    except Exception as e:
        import traceback
        job.status = 'failed'
        debug_str = "\n".join(debug_info) if 'debug_info' in locals() else "No debug info available"
        job.error_message = f"{str(e)}\n\nDebug Info:\n{debug_str}\n\n{traceback.format_exc()}"
        job.save()
        logger.error(f"Job {job_id} failed: {str(e)}")
        logger.error(f"Debug info: {debug_str}")
        logger.error(f"Traceback: {traceback.format_exc()}")


def finalize_job(job_id):
    """Finalize a job by creating the final PDF from processed images"""
    try:
        job = ProcessingJob.objects.get(id=job_id)
        
        # Set job status to finalizing
        job.status = 'finalizing'
        job.save()
        
        # Get the output directory and PDF path
        output_dir = os.path.join(settings.MEDIA_ROOT, 'processed', str(job.id))
        output_pdf = os.path.join(output_dir, f"{job.title or 'processed'}.pdf")
        
        # Collect all pages
        outfiles = []
        for page in job.pages.all().order_by('page_number'):
            # Determine which image to use for this page
            if page.adjusted_image:  # User adjusted the crop
                image_path = os.path.join(settings.MEDIA_ROOT, page.adjusted_image)
                outfiles.append((page.page_number, image_path))
            elif page.cropped_image:  # Use auto-cropped image
                image_path = os.path.join(settings.MEDIA_ROOT, page.cropped_image)
                outfiles.append((page.page_number, image_path))
        
        # Sort by page number
        outfiles.sort(key=lambda x: x[0])
        
        if not outfiles:
            raise ValueError("No processed pages found for finalization")

        # Save PDF
        metadata = None  # We don't have metadata in this context
        
        # Prepare output paths for hocr and txt files if OCR is enabled
        outhocr = None
        outtxt = None
        if job.ocr:
            hocr_filename = f"output_{job.id}.hocr.gz"
            txt_filename = f"output_{job.id}.txt"
            outhocr = os.path.join(os.path.dirname(output_pdf), hocr_filename)
            outtxt = os.path.join(os.path.dirname(output_pdf), txt_filename)
        
        save_pdf(outfiles, metadata, job.language, output_pdf, job.ocr, outhocr, outtxt)

        # Update job with output file
        relative_path = os.path.relpath(output_pdf, settings.MEDIA_ROOT)
        job.output_file = relative_path
        job.status = 'completed'
        job.save()

    except Exception as e:
        import traceback
        job = ProcessingJob.objects.get(id=job_id)
        job.status = 'failed'
        job.error_message = f"Error during finalization: {str(e)}\n\n{traceback.format_exc()}"
        job.save()


@require_http_methods(["POST"])
@csrf_protect
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
