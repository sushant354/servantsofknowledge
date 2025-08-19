from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('jobs/', views.all_jobs, name='all_jobs'),
    path('register/', views.register, name='register'),
    path('job/<uuid:job_id>/', views.job_detail, name='job_detail'),
    path('job/<uuid:job_id>/download/', views.job_download, name='job_download'),
    path('job/<uuid:job_id>/output-directory/', views.job_output_directory, name='job_output_directory'),
    path('job/<uuid:job_id>/review/', views.job_review, name='job_review'),
    path('job/<uuid:job_id>/page/<int:page_number>/', views.page_editor, name='page_editor'),
    path('job/<uuid:job_id>/page/<int:page_number>/update-crop/', views.update_crop, name='update_crop'),
    path('job/<uuid:job_id>/page/<int:page_number>/save-snip/', views.save_snip, name='save_snip'),
    path('job/<uuid:job_id>/status/', views.job_status, name='job_status'),
]