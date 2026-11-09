from django.urls import path

from . import views

urlpatterns = [
    path('structure/', views.academics_home, name='academics_home'),
    path('structure/offerings/<int:pk>/enrolments/', views.offering_enrolments, name='offering_enrolments'),
    path('structure/offerings/<int:pk>/enrol/', views.offering_enrol, name='offering_enrol'),
    path('structure/offerings/<int:pk>/withdraw/<int:enrolment_id>/', views.offering_withdraw, name='offering_withdraw'),
    path('structure/offerings/<int:pk>/import/', views.offering_import, name='offering_import'),
    path('structure/<str:key>/', views.academics_list, name='academics_list'),
    path('structure/<str:key>/add/', views.academics_edit, name='academics_add'),
    path('structure/<str:key>/<int:pk>/edit/', views.academics_edit, name='academics_edit'),
    path('structure/<str:key>/<int:pk>/delete/', views.academics_delete, name='academics_delete'),
]
