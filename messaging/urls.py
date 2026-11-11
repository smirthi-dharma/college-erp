from django.urls import path

from . import views

urlpatterns = [
    path('messaging/', views.settings_page, name='messaging_settings'),
    path('messaging/report/', views.daily_report, name='messaging_report'),
    path('messaging/report.csv', views.daily_report_csv, name='messaging_report_csv'),
    path('messaging/usage/', views.usage_report, name='messaging_usage'),
    path('messaging/usage.csv', views.usage_csv, name='messaging_usage_csv'),
    path('messaging/consent/', views.consent_page, name='messaging_consent'),
    path('messaging/consent/<int:pk>/', views.consent_history, name='messaging_consent_history'),
    path('messaging/consent/scan/<int:pk>/', views.consent_scan, name='messaging_consent_scan'),
    path('webhooks/whatsapp/', views.webhook, name='whatsapp_webhook'),
]
