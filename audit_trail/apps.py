from django.apps import AppConfig


class AuditTrailConfig(AppConfig):
    name = 'audit_trail'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import signals
        signals.connect()
