from django.urls import path

from . import views

urlpatterns = [
    path('import/', views.import_home, name='import_home'),
    path('import/confirm/', views.import_confirm, name='import_confirm'),
    path('import/problems/', views.import_errors, name='import_errors'),
    path('import/batch/<int:pk>/rollback/', views.import_rollback, name='import_rollback'),
    path('import/<str:kind>/', views.import_upload, name='import_upload'),
    path('import/<str:kind>/template/', views.import_template, name='import_template'),
]
