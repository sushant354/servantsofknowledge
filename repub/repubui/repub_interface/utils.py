import os
import cv2
import numpy as np
from PIL import Image
from io import BytesIO
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

def process_and_save_image(image_data, output_path, quality=95):
    """
    Process and save an image with optimized quality.
    
    Args:
        image_data: Raw image data or numpy array
        output_path: Path where to save the processed image
        quality: JPEG quality (1-100)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # If image_data is raw bytes, convert to numpy array
        if isinstance(image_data, bytes):
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        else:
            img = image_data
            
        if img is None or img.size == 0:
            raise ValueError("Invalid image data")
            
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Save image with quality optimization
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        return cv2.imwrite(output_path, img, encode_params)
        
    except Exception as e:
        print(f"Error processing image: {str(e)}")
        return False

def create_and_save_thumbnail(image_path, thumbnail_field, filename, max_size=(300, 300)):
    """
    Create and save a thumbnail for an image.
    
    Args:
        image_path: Path to the original image
        thumbnail_field: Model's ImageField to save the thumbnail to
        filename: Name for the thumbnail file
        max_size: Maximum dimensions for the thumbnail (width, height)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Create thumbnail
        img = Image.open(image_path)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Save to BytesIO
        thumbnail_io = BytesIO()
        img.save(thumbnail_io, format='JPEG', quality=85)
        
        # Create and save thumbnail file
        thumbnail_file = ContentFile(thumbnail_io.getvalue())
        thumbnail_field.save(
            filename,
            InMemoryUploadedFile(
                thumbnail_file,
                None,
                filename,
                'image/jpeg',
                len(thumbnail_io.getvalue()),
                None
            )
        )
        return True
        
    except Exception as e:
        print(f"Error creating thumbnail: {str(e)}")
        return False

def get_next_page_for_review(job, current_page_number):
    """
    Get the next page that needs review.
    
    Args:
        job: ProcessingJob instance
        current_page_number: Current page number
        
    Returns:
        PageImage or None: Next page that needs review, if any
    """
    return job.pages.filter(
        page_number__gt=current_page_number,
        needs_review=True
    ).order_by('page_number').first() 