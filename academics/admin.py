"""Platform-staff admin pages (the admin itself is locked to platform staff with 2FA, see admin_site.py)."""
from django.contrib import admin

from . import models

for model in (models.Department, models.Programme, models.Regulation, models.Batch, models.Section,
              models.Subject, models.AcademicTerm, models.SubjectOffering, models.Enrolment):
    admin.site.register(model)
