from django.contrib import admin
from .models import ProcessingJob

@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'created_at', 'status', 'input_type', 'language')
    list_filter = ('status', 'input_type', 'crop', 'deskew', 'ocr', 'dewarp')
    search_fields = ('title', 'id')
    readonly_fields = ('id', 'created_at')
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'title', 'created_at', 'status', 'input_file', 'input_type', 'output_file')
        }),
        ('Processing Options', {
            'fields': ('language', 'crop', 'deskew', 'ocr', 'dewarp', 'rotate_type', 'reduce_factor')
        }),
        ('Advanced Options', {
            'fields': ('xmax', 'ymax', 'maxcontours')
        }),
        ('Error Information', {
            'fields': ('error_message',)
        }),
    )