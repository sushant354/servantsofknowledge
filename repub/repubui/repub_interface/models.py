from django.db import models
from django.contrib.auth.models import User
import os
import uuid
import json
from django.conf import settings


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    email_confirmed = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.user.username} - Email Confirmed: {self.email_confirmed}"

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
                                  default='images')

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
    reduce_factor = models.FloatField(null=True, blank=True, default = "0.2")

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
        return os.path.join(settings.MEDIA_ROOT, 'uploads', str(self.id), 'extracted')

    def get_output_dir(self):
        return os.path.join(settings.MEDIA_ROOT, 'processed', str(self.id))
        
    def get_outimg_dir(self):
        return os.path.join(settings.MEDIA_ROOT, 'processed', str(self.id), 'output')
        
    def get_review_dir(self):
        return os.path.join(settings.MEDIA_ROOT, 'review', str(self.id))
        
    def get_thumbnail_dir(self):
        return os.path.join(settings.MEDIA_ROOT, 'processed',  str(self.id), 'thumbnails')

