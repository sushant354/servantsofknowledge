from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import ProcessingJob

@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'created_at', 'status', 'input_type', 'language', 'view_job_link')
    list_filter = ('status', 'input_type', 'crop', 'deskew', 'ocr', 'dewarp')
    search_fields = ('title', 'id')
    readonly_fields = ('id', 'created_at', 'view_job_link')
    
    def view_job_link(self, obj):
        url = reverse('job_detail', args=[obj.id])
        return format_html('<a href="{}" target="_blank">View Job Detail</a>', url)
    view_job_link.short_description = 'View Job'
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'title', 'created_at', 'status', 'input_file', 'input_type', 'output_file', 'view_job_link')
        }),
        ('Processing Options', {
            'fields': ('language', 'crop', 'deskew', 'ocr', 'dewarp', 'rotate_type', 'reduce_factor')
        }),
        ('Advanced Options', {
            'fields': ('xmaximum', 'ymax', 'maxcontours')
        }),
        ('Error Information', {
            'fields': ('error_message',)
        }),
    )
