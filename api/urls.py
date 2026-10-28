from django.urls import path

from .views import (
    filestore_deregister,
    filestore_detail,
    filestore_file_download,
    filestore_file_upload,
    filestore_files,
    filestore_list,
    filestore_register,
)

urlpatterns = [
    path("filestores/", filestore_list, name="filestore-list"),
    path("filestores/register", filestore_register, name="filestore-register"),
    path("filestores/<str:uid>/", filestore_detail, name="filestore-detail"),
    path("filestores/<str:uid>/deregister", filestore_deregister, name="filestore-deregister"),
    path("filestores/<str:uid>/files/", filestore_files, name="filestore-files"),
    path("filestores/<str:uid>/files/upload", filestore_file_upload, name="filestore-file-upload"),
    path("filestores/<str:uid>/files/download", filestore_file_download, name="filestore-file-download"),
]