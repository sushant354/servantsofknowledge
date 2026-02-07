import os
import cv2
import time
import logging
import threading
import shutil

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from .models import ProcessingJob
from repub import process_raw
from repub.utils.scandir import Scandir, get_pagenum
from repub.imgfuncs.dewarp import dewarp

logger = logging.getLogger('repubui.review')

def generate_files_for_review(scandir, thumbdir, job):
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        filename =  os.path.basename(outfile)
        cv2.imwrite(outfile, img)

        thumbnail = process_raw.get_thumbnail(img)
        thumbfile = os.path.join(thumbdir, filename)
        cv2.imwrite(thumbfile, thumbnail)

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



