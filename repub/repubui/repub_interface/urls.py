from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('jobs/', views.all_jobs, name='all_jobs'),
    path('register/', views.register, name='register'),
    path('activate/<str:uidb64>/<str:token>/', views.activate_account, name='activate_account'),
    path('job/<uuid:job_id>/', views.job_detail, name='job_detail'),
    path('job/<uuid:job_id>/download/', views.job_download, name='job_download'),
    path('job/<uuid:job_id>/output-directory/', views.job_output_directory, name='job_output_directory'),
    path('job/<uuid:job_id>/output-directory/<path:subpath>/', views.job_output_directory, name='job_output_directory_subpath'),
    path('job/<uuid:job_id>/input-directory/', views.job_input_directory, name='job_input_directory'),
    path('job/<uuid:job_id>/input-directory/<path:subpath>/', views.job_input_directory, name='job_input_directory_subpath'),
    path('job/<uuid:job_id>/review/', views.job_review, name='job_review'),
    path('job/<uuid:job_id>/page/<int:pagenum>/', views.page_editor, name='page_editor'),
    path('job/<uuid:job_id>/page/<int:page_number>/save-snip/', views.save_snip, name='save_snip'),
    path('job/<uuid:job_id>/finalize/', views.finalize_job, name='finalize_job'),
    path('job/<uuid:job_id>/reject-review/', views.reject_review, name='reject_review'),
    path('job/<uuid:job_id>/retry/', views.retry_job, name='retry_job'),
    path('job/<uuid:job_id>/status/', views.job_status, name='job_status'),
    path('job/<uuid:job_id>/stop/', views.stop_job, name='stop_job'),
    path('api-token/', views.api_token_management, name='api_token'),
]
