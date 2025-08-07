from django.db import models
from django.contrib.auth.models import User
import os
import uuid
import json


def get_upload_path(instance, filename):
    return f'uploads/{instance.id}/{filename}'


def get_output_path(instance, filename):
    return f'processed/{instance.id}/{filename}'


def get_thumbnail_path(instance, filename):
    return f'thumbnails/{instance.job.id}/{filename}'


class ProcessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='processing_jobs', null=True, blank=True)
    title = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='pending',
                              choices=[
                                  ('pending', 'Pending'),
                                  ('processing', 'Processing'),
                                  ('completed', 'Completed'),
                                  ('failed', 'Failed'),
                                  ('reviewing', 'Awaiting Review'),
                                  ('finalizing', 'Finalizing')
                              ])

    # Input options
    input_file = models.FileField(upload_to=get_upload_path, blank=True, null=True)
    input_type = models.CharField(max_length=10,
                                  choices=[('pdf', 'PDF'), ('images', 'Images')],
                                  default='pdf')

    # Processing options
    language = models.CharField(max_length=100, default='eng')
    crop = models.BooleanField(default=True)
    deskew = models.BooleanField(default=True)
    ocr = models.BooleanField(default=True)
    dewarp = models.BooleanField(default=False)
    draw_contours = models.BooleanField(default=False)
    gray = models.BooleanField(default=False)
    rotate_type = models.CharField(max_length=20, default='vertical',
                                   choices=[
                                       ('horizontal', 'Horizontal'),
                                       ('vertical', 'Vertical'),
                                       ('overall', 'Overall')
                                   ])
    reduce_factor = models.FloatField(null=True, blank=True)
    
    # Internet Archive directory option (default enabled)
    iadir = models.BooleanField(default=True, help_text='Use Internet Archive directory structure for output')

    # Advanced options
    xmaximum = models.IntegerField(default=30)
    ymax = models.IntegerField(default=60)
    maxcontours = models.IntegerField(default=5)

    # Output
    output_file = models.FileField(upload_to=get_output_path, blank=True, null=True)
    error_message = models.TextField(blank=True)
    
    # Review
    needs_review = models.BooleanField(default=False)
    reviewed = models.BooleanField(default=False)

    def __str__(self):
        return f"Job {self.id} - {self.status}"

    def get_input_dir(self):
        return os.path.join('media', 'uploads', str(self.id))

    def get_output_dir(self):
        return os.path.join('media', 'processed', str(self.id))
        
    def get_thumbnail_dir(self):
        return os.path.join('media', 'thumbnails', str(self.id))
        
    def get_ia_directory_structure(self):
        """Get Internet Archive directory structure with all files"""
        if not self.iadir:
            return None
            
        from django.conf import settings
        ia_path = os.path.join(settings.MEDIA_ROOT, 'ia_output', str(self.id))
        
        if not os.path.exists(ia_path):
            return None
            
        structure = {
            'base_path': ia_path,
            'relative_path': f'ia_output/{self.id}',
            'files': []
        }
        
        # Get all files in IA directory recursively
        for root, dirs, files in os.walk(ia_path):
            for file in files:
                full_path = os.path.join(root, file)
                relative_to_ia = os.path.relpath(full_path, ia_path)
                relative_to_media = os.path.relpath(full_path, settings.MEDIA_ROOT)
                
                file_info = {
                    'name': file,
                    'path': relative_to_ia,
                    'full_path': full_path,
                    'media_path': relative_to_media,
                    'size': os.path.getsize(full_path) if os.path.exists(full_path) else 0,
                    'directory': os.path.dirname(relative_to_ia) or '.'
                }
                structure['files'].append(file_info)
                
        # Sort files by directory then name
        structure['files'].sort(key=lambda x: (x['directory'], x['name']))
        
        return structure


class PageImage(models.Model):
    job = models.ForeignKey(ProcessingJob, on_delete=models.CASCADE, related_name='pages')
    page_number = models.IntegerField()
    
    # Original image path (relative to MEDIA_ROOT)
    original_image = models.CharField(max_length=255)
    
    # Auto-cropped image path (relative to MEDIA_ROOT)
    cropped_image = models.CharField(max_length=255, blank=True, null=True)
    
    # User-adjusted image path
    adjusted_image = models.CharField(max_length=255, blank=True, null=True)
    
    # Thumbnails
    original_thumbnail = models.ImageField(upload_to=get_thumbnail_path, blank=True, null=True)
    cropped_thumbnail = models.ImageField(upload_to=get_thumbnail_path, blank=True, null=True)
    
    # Cropping data
    auto_crop_box = models.TextField(blank=True, null=True)
    user_crop_box = models.TextField(blank=True, null=True)
    
    # Status
    needs_review = models.BooleanField(default=False)
    reviewed = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['page_number']
        unique_together = ['job', 'page_number']
    
    def __str__(self):
        return f"Page {self.page_number} of Job {self.job.id}"
    
    def get_auto_crop_box(self):
        if self.auto_crop_box:
            return json.loads(self.auto_crop_box)
        return None
    
    def set_auto_crop_box(self, box):
        if box:
            self.auto_crop_box = json.dumps(box)
        else:
            self.auto_crop_box = None
    
    def get_user_crop_box(self):
        if self.user_crop_box:
            return json.loads(self.user_crop_box)
        return None
    
    def set_user_crop_box(self, box):
        if box:
            self.user_crop_box = json.dumps(box)
        else:
            self.user_crop_box = None
