import os
import cv2
import json
import time
import logging
import shutil
import zipfile

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, FileResponse

from .models import ProcessingJob
from .users import login_or_token_required
from repub import process_raw
from repub.utils.scandir import get_pagenum, Scandir
from repub.imgfuncs.dewarp import dewarp

logger = logging.getLogger('repubui.review')

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
            # Set status to 'preparing_review' and start Celery task
            job.status = 'preparing_review'
            job.save()

            from .tasks import prepare_review_task
            prepare_review_task.delay(str(job.id))

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


@login_required
@require_http_methods(["POST"])
def submit_for_correction(request, job_id):
    """Mark pages needing correction, copy them to corrections folder with scandata.json, set status to under_correction"""
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    # Parse marked pages and their messages from POST data
    correction_notes = {}
    for key, value in request.POST.items():
        if key.startswith('correction_message_'):
            page_num = key.replace('correction_message_', '')
            if f'correction_page_{page_num}' in request.POST:
                correction_notes[page_num] = value.strip() or 'Needs correction'

    if not correction_notes:
        messages.warning(request, 'No pages were marked for correction.')
        return redirect('job_review', job_id=job_id)

    # Create corrections directory
    corrections_dir = job.get_corrections_dir()
    os.makedirs(corrections_dir, exist_ok=True)

    # Copy marked images to corrections folder
    review_imgdir = os.path.join(job.get_review_dir(), 'images')
    for page_num_str in correction_notes:
        page_num = int(page_num_str)
        filename = f"{page_num:04d}.jpg"
        src = os.path.join(review_imgdir, filename)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(corrections_dir, filename))

    # Write scandata.json with correction notes
    scandata = {
        'corrections': {
            page_num: {'message': msg} for page_num, msg in correction_notes.items()
        }
    }

    # Also include original scandata if it exists
    input_dir = job.get_input_dir()
    original_scandata_path = os.path.join(input_dir, 'scandata.json')
    if os.path.exists(original_scandata_path):
        with open(original_scandata_path, 'r', encoding='utf8') as f:
            original_scandata = json.loads(f.read())
        scandata['original'] = original_scandata

    scandata_path = os.path.join(corrections_dir, 'scandata.json')
    with open(scandata_path, 'w', encoding='utf8') as f:
        json.dump(scandata, f, indent=2)

    # Save correction notes to model and set status
    job.correction_notes = correction_notes
    job.status = 'under_correction'
    job.save()

    logger.info(f"Job {job.id} submitted for correction. Pages: {list(correction_notes.keys())}")
    messages.success(request, f'Job submitted for correction. {len(correction_notes)} page(s) marked.')
    return redirect('job_detail', job_id=job_id)


@login_required
@require_http_methods(["GET"])
def download_corrections(request, job_id):
    """Download the corrections folder as a zip file"""
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    corrections_dir = job.get_corrections_dir()
    if not os.path.exists(corrections_dir):
        messages.error(request, 'No corrections folder found.')
        return redirect('job_detail', job_id=job_id)

    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    tmp_path = tmp.name
    tmp.close()

    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filename in os.listdir(corrections_dir):
            filepath = os.path.join(corrections_dir, filename)
            if os.path.isfile(filepath):
                zf.write(filepath, filename)

    title_slug = (job.title or 'corrections').replace(' ', '_')[:50]
    response = FileResponse(open(tmp_path, 'rb'), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{title_slug}_corrections.zip"'
    # Clean up temp file after response is sent
    response.file_to_stream_with_cleanup = tmp_path
    return response


def _process_correction_zip(job, correction_file):
    """
    Core logic: extract corrected images from zip into the resolved input directory,
    clean up review/output dirs, and resubmit the job for processing.

    Returns (success: bool, error_message: str or None, files_copied: int)
    """
    import tempfile

    if not correction_file.name.endswith('.zip'):
        return False, 'Please upload a ZIP file.', 0

    input_dir = job.get_input_dir()
    if not os.path.exists(input_dir):
        os.makedirs(input_dir, exist_ok=True)

    # Resolve the actual input directory (Scandir traverses into subdirectories)
    scandir = Scandir(input_dir, None, None)
    resolved_input_dir = scandir.indir

    # Save the uploaded zip temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
        for chunk in correction_file.chunks():
            tmp.write(chunk)
        tmp_path = tmp.name

    files_copied = 0
    try:
        with zipfile.ZipFile(tmp_path, 'r') as zf:
            for member in zf.namelist():
                # Skip directories and hidden files
                if member.endswith('/') or os.path.basename(member).startswith('.'):
                    continue
                # Only process image files
                basename = os.path.basename(member)
                if not basename.lower().endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.jp2')):
                    continue
                # Extract image data and write to resolved input directory
                with zf.open(member) as src:
                    dest_path = os.path.join(resolved_input_dir, basename)
                    with open(dest_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                files_copied += 1
                logger.info(f"Copied corrected image {basename} to {resolved_input_dir} for job {job.id}")
    except zipfile.BadZipFile:
        os.unlink(tmp_path)
        return False, 'The uploaded file is not a valid ZIP file.', 0
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Clean up review and corrections directories
    review_dir = job.get_review_dir()
    if os.path.exists(review_dir):
        shutil.rmtree(review_dir)

    # Clean up output directory for reprocessing
    output_dir = job.get_output_dir()
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

    img_dir = os.path.join(output_dir, 'output')
    os.makedirs(img_dir, exist_ok=True)
    thumbnaildir = os.path.join(output_dir, 'thumbnails')
    os.makedirs(thumbnaildir, exist_ok=True)

    # Clear correction notes and resubmit for processing
    job.correction_notes = {}
    job.status = 'pending'
    job.error_message = ''
    job.save()

    from .tasks import run_job_task
    run_job_task.delay(str(job.id))

    logger.info(f"Job {job.id} correction submitted, requeued for processing")
    return True, None, files_copied


@login_required
@require_http_methods(["POST"])
def submit_correction_zip(request, job_id):
    """Accept a zip file of corrected images, copy to input directory, and resubmit for processing"""
    if request.user.is_staff:
        job = get_object_or_404(ProcessingJob, id=job_id)
    else:
        job = get_object_or_404(ProcessingJob, id=job_id, user=request.user)

    if job.status != 'under_correction':
        messages.error(request, 'Job is not under correction.')
        return redirect('job_detail', job_id=job_id)

    correction_file = request.FILES.get('correction_zip')
    if not correction_file:
        messages.error(request, 'No correction file uploaded.')
        return redirect('job_detail', job_id=job_id)

    success, error, _ = _process_correction_zip(job, correction_file)
    if not success:
        messages.error(request, error)
        return redirect('job_detail', job_id=job_id)

    messages.success(request, f'Corrections uploaded. Job "{job.title or "Untitled"}" has been resubmitted for processing.')
    return redirect('job_detail', job_id=job_id)


@csrf_exempt
@login_or_token_required
@require_http_methods(["POST"])
def api_submit_correction_zip(request, job_id):
    """API endpoint: submit a corrections zip file for a job under correction.

    Expects a multipart/form-data POST with a 'correction_zip' file field.
    Authenticated via session or Authorization: Token <token> header.
    """
    if request.user.is_staff:
        job = ProcessingJob.objects.filter(id=job_id).first()
    else:
        job = ProcessingJob.objects.filter(id=job_id, user=request.user).first()

    if not job:
        return JsonResponse({'success': False, 'error': 'Job not found'}, status=404)

    if job.status != 'under_correction':
        return JsonResponse({
            'success': False,
            'error': f'Job is not under correction. Current status: {job.status}'
        }, status=400)

    correction_file = request.FILES.get('correction_zip')
    if not correction_file:
        return JsonResponse({'success': False, 'error': 'No correction_zip file provided'}, status=400)

    success, error, files_copied = _process_correction_zip(job, correction_file)
    if not success:
        return JsonResponse({'success': False, 'error': error}, status=400)

    return JsonResponse({
        'success': True,
        'message': f'Corrections uploaded. {files_copied} file(s) copied. Job resubmitted for processing.',
        'job_id': str(job.id),
        'status': job.status
    })
