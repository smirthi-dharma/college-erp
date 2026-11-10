from django.urls import path

from . import views

urlpatterns = [
    path('students/', views.student_list, name='student_list'),
    path('students/me/', views.student_me, name='student_me'),
    path('students/<int:pk>/', views.student_detail, name='student_detail'),
]
