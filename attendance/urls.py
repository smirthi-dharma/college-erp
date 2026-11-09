from django.urls import path

from . import views

urlpatterns = [
    path('attendance/', views.attendance_home, name='attendance_home'),
    path('attendance/class/<int:offering_id>/', views.attendance_class, name='attendance_class'),
    path('attendance/class/<int:offering_id>/saved/', views.attendance_summary, name='attendance_summary'),
    path('attendance/shortage/', views.shortage_report, name='shortage_report'),
    path('attendance/mine/', views.my_attendance, name='my_attendance'),
    path('attendance/on-duty/', views.onduty_review, name='onduty_review'),
    path('attendance/on-duty/<int:report_id>/', views.onduty_decide, name='onduty_decide'),
    path('attendance/condonation/', views.condonation_list, name='condonation_list'),
    path('attendance/condonation/<int:pk>/revoke/', views.condonation_revoke, name='condonation_revoke'),
    path('attendance/substitutes/', views.substitute_list, name='substitute_list'),
    path('attendance/substitutes/<int:pk>/delete/', views.substitute_delete, name='substitute_delete'),
    path('attendance/rules/', views.rules_list, name='rules_list'),
    path('attendance/rules/<int:regulation_id>/', views.rule_edit, name='rule_edit'),
]
