import os
import re
import shutil
import zipfile
import logging
import tempfile

import cv2
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from repub import process_raw
from repub.utils.scandir import Scandir
from repub.utils import pdfs
from .models import ProcessingJob

logger = logging.getLogger('repubui.tasks')


def get_pagenum(filename):
    pagenum = None
    reobj = re.match('(?P<pagenum>\\d{4})\\.', filename)
    if reobj:
        groupdict = reobj.groupdict('pagenum')
        pagenum   = int(groupdict['pagenum'])
    return pagenum


def derive_pdf(job, identifier, metadata, derive_dir, derive_reduce_factor, logger):
    temp_dir = None
    derive_loghandle = None
    job.status = 'deriving'
    job.derive_started = timezone.now()
    job.save()
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

        logger.info(f"Regenerating PDF with OCR for {len(outfiles)} pages")

        # If reduce_factor is specified, create reduced images
        if derive_reduce_factor is not None:
            temp_dir = tempfile.mkdtemp(prefix=f'derive_reduced_{job.id}_')
            logger.info(f"Reducing images by factor {derive_reduce_factor} in temporary directory: {temp_dir}")

            reduced_outfiles = []
            for pagenum, outfile in outfiles:
                # Read the image
                img = cv2.imread(outfile)
                if img is None:
                    logger.warning(f"Failed to read image {outfile}, using original")
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
                logger.info(f"Reduced page {pagenum} from {width}x{height} to {new_width}x{new_height}")

            # Use reduced images for PDF generation
            outfiles = reduced_outfiles
            logger.info(f"Using {len(outfiles)} reduced images for PDF generation")

        # Generate PDF with OCR, HOCR, and text
        pdfs.save_pdf(outfiles, metadata, job.language, pdf_dest,
                      True, hocr_dest, text_dest, logger)

        logger.info(f"Generated PDF with OCR in derive directory: {pdf_dest}")
        logger.info(f"Generated HOCR in derive directory: {hocr_dest}")
        logger.info(f"Generated text in derive directory: {text_dest}")

        # Clean up temporary directory if it was created
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"Cleaned up temporary directory: {temp_dir}")

        # Close the derive log file handle
        if derive_loghandle:
            derive_loghandle.close()

    except Exception as e:
        logger.error(f"Error generating PDF for job {job.id}: {str(e)}", exc_info=True)

        # Clean up temporary directory if it exists
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up temporary directory: {str(cleanup_error)}")

        # Close the derive log file handle
        if derive_loghandle:
            derive_loghandle.close()

        job.status = 'derive_failed'
        job.save()
        logger.error(f"Updated job {job.id} to derive_failed")
        raise


@shared_task
def derive_job_task(job_id):
    """Celery task to derive a job - runs outside the uwsgi worker lifecycle"""
    try:
        job = ProcessingJob.objects.get(id=job_id)
    except ProcessingJob.DoesNotExist:
        logger = logging.getLogger('repub.derive')
        logger.error(f"Job {job_id} does not exist, skipping derive task")
        return
    derive_reduce_factor = job.derive_reduce_factor

    logger = logging.getLogger(f'repub.derive.{job.id}')

    logger.info(f"Started deriving job {job.id}")
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
            shutil.rmtree(derive_dir)
            logger.info(f"Cleaned up existing derive directory: {derive_dir}")
        os.makedirs(derive_dir, exist_ok=True)
        # Clear any existing handlers
        logger.handlers.clear()

        # Prevent propagation to parent loggers to avoid writing to closed streams
        logger.propagate = False

        # Create file handler for derive log
        derive_logfile = os.path.join(derive_dir, 'derive.log')
        derive_loghandle = open(derive_logfile, 'a', encoding='utf-8')
        derive_file_handler = logging.StreamHandler(derive_loghandle)
        logger.addHandler(derive_file_handler)

        logger.info(f"Created derive directory: {derive_dir}")

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
            derive_pdf(job, identifier, metadata, derive_dir, derive_reduce_factor, logger)
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
    job.processing_started_at = timezone.now()
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

    # Prevent propagation to parent loggers to avoid writing to closed streams
    # in uwsgi background threads
    job_logger.propagate = False

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

    # Check if identifier is already being processed or derived
    identifier = metadata.get('/Identifier')
    if identifier:
        # Check if there's already a job with this identifier being processed
        existing_job = ProcessingJob.objects.filter(
            identifier=identifier
        ).exclude(id=job.id).first()

        if existing_job:
            job.status = 'failed'
            job.error_message = f'Job rejected: Identifier "{identifier}" is already being processed by <a href="/job/{existing_job.id}/">another job</a>.'
            job.save()
            job_logger.error(f"Job rejected: Identifier '{identifier}' is already being processed by job {existing_job.id}")
            loghandle.close()
            return

        derive_base_dir = os.path.join(settings.MEDIA_ROOT, 'derived')
        derive_dir = os.path.join(derive_base_dir, identifier)

        if os.path.exists(derive_dir):
            job.status = 'failed'
            job.error_message = f'Job rejected: The identifier "<a href="/item/{identifier}/">{identifier}</a>" has already been derived.'
            job.save()
            job_logger.error(f"Job rejected: Identifier '{identifier}' already exists in derived directory: {derive_dir}")
            loghandle.close()
            return

    title = metadata.get('/Title')
    if title:
        job.title = title
        job.save()

    # Save author to job
    author = metadata.get('/Creator')
    if author:
        job.author = author

    # Save identifier to job
    job.identifier = identifier
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


@shared_task
def run_job_task(job_id):
    """Celery task to run a processing job"""
    try:
        job = ProcessingJob.objects.get(id=job_id)
    except ProcessingJob.DoesNotExist:
        logger.error(f"Job {job_id} does not exist, skipping run_job task")
        return

    logger.info(f"Starting run_job_task for job {job.id}")
    try:
        process_job(job)
    except Exception as e:
        logger.exception('Error in process_job %s error: %s', job.id, e)
        job.status = 'failed'
        job.error_message = str(e)
        job.save()
