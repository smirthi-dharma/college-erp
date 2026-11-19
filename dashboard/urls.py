from django.urls import path

from . import views

urlpatterns = [
    path('dashboard/', views.principal_dashboard, name='principal_dashboard'),
    path('dashboard/export/', views.principal_export, name='principal_export'),
    path('management/users/', views.management_users, name='management_users'),
    path('management/users/add/', views.management_user_add, name='management_user_add'),
    path('management/users/<int:pk>/edit/', views.management_user_edit, name='management_user_edit'),
    path('management/users/<int:pk>/active/', views.management_user_active, name='management_user_active'),
]
