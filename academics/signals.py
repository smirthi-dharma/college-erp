from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from main_app.models import Student

from . import services
from .models import SubjectOffering


@receiver(pre_save, sender=Student)
def remember_old_section(sender, instance, raw=False, **kwargs):
    instance._old_section_id = None
    if instance.pk and not raw:
        instance._old_section_id = (
            Student.objects.filter(pk=instance.pk).values_list('section_id', flat=True).first())


@receiver(post_save, sender=Student)
def student_section_changed(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    old = getattr(instance, '_old_section_id', None)
    if created or old != instance.section_id:
        services.sync_student_section(instance, None if created else old)


@receiver(post_save, sender=SubjectOffering)
def offering_created(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        services.enrol_section_in_core(instance)
