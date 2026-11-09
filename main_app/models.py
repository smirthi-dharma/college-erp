from datetime import datetime, timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models.functions import Lower
from django.db.models.signals import post_save
from django.dispatch import receiver


class CustomUserManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = CustomUser(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("user_type", CustomUser.COLLEGE_ADMIN)

        if not (extra_fields["is_staff"] and extra_fields["is_superuser"]):
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    # Stored as strings; compare with these constants, never with bare 1/2/3.
    # "1" is the college-wide administrator (it was called HOD in the inherited code; the stored value is unchanged).
    # "4" (Head of department) has no pages yet: it is refused everywhere until the scoped role is built.
    COLLEGE_ADMIN, STAFF, STUDENT, HEAD_OF_DEPARTMENT = "1", "2", "3", "4"
    USER_TYPE = ((COLLEGE_ADMIN, "College admin"), (STAFF, "Staff"), (STUDENT, "Student"),
                 (HEAD_OF_DEPARTMENT, "Head of department"))
    GENDER = [("M", "Male"), ("F", "Female")]
    
    
    username = None  # Removed username, using email instead
    email = models.EmailField(unique=True)
    # Least privilege by default: a user created without a role is a student, never an administrator.
    user_type = models.CharField(default=STUDENT, choices=USER_TYPE, max_length=1)
    gender = models.CharField(max_length=1, choices=GENDER)
    profile_pic = models.ImageField()
    address = models.TextField()
    phone = models.CharField(max_length=13, blank=True, default="")  # +91XXXXXXXXXX
    fcm_token = models.TextField(default="")  # For firebase notifications
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    def save(self, *args, **kwargs):
        # Callers pass 1 or "1"; always store the string so every comparison agrees.
        self.user_type = str(self.user_type)
        super().save(*args, **kwargs)

    def __str__(self):
        return  self.first_name + " " + self.last_name


class Admin(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)



def _clean_identifier(value):
    """Roll numbers and employee IDs: trimmed, and blank means 'none' (so uniqueness ignores it)."""
    value = (value or '').strip()
    return value or None


class Student(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    # Free text (any college's scheme), trimmed; unique ignoring case ("21A001" = "21a001").
    roll_number = models.CharField(max_length=30, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    # Programme and batch follow from the section (section.batch.programme).
    section = models.ForeignKey('academics.Section', on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('roll_number'), name='uniq_student_roll_number_ci')]

    def __str__(self):
        return self.admin.last_name + ", " + self.admin.first_name

    def save(self, *args, **kwargs):
        self.roll_number = _clean_identifier(self.roll_number)
        super().save(*args, **kwargs)

def expiry():
    # Kept only because migration 0001 references it; the library models are gone (migration 0004).
    return datetime.today() + timedelta(days=14)


class Staff(models.Model):
    department = models.ForeignKey('academics.Department', on_delete=models.PROTECT, null=True, blank=True)
    employee_id = models.CharField(max_length=30, null=True, blank=True)  # trimmed, unique ignoring case
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('employee_id'), name='uniq_staff_employee_id_ci')]

    def __str__(self):
        return self.admin.first_name + " " +  self.admin.last_name

    def save(self, *args, **kwargs):
        self.employee_id = _clean_identifier(self.employee_id)
        super().save(*args, **kwargs)



class Attendance(models.Model):
    """One class held: an offering on a date (and period, for two classes of one offering in a day)."""
    offering = models.ForeignKey('academics.SubjectOffering', on_delete=models.PROTECT)
    date = models.DateField()
    period = models.PositiveSmallIntegerField(default=1)
    taken_by = models.ForeignKey(CustomUser, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    submission_key = models.CharField(max_length=40, blank=True, default="")  # makes a retried submit idempotent
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['offering', 'date', 'period'], name='uniq_class_per_period')]


class AttendanceReport(models.Model):
    PRESENT, ABSENT, ON_DUTY = 'present', 'absent', 'on_duty'
    STATUS = [(PRESENT, 'Present'), (ABSENT, 'Absent'), (ON_DUTY, 'On duty')]

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS, default=PRESENT)
    needs_review = models.BooleanField(default=False)  # on-duty marked by a teacher awaits the college admin
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['attendance', 'student'], name='uniq_report_per_student')]


class LeaveReportStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.CharField(max_length=60)
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeaveReportStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    date = models.CharField(max_length=60)
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Guardian(models.Model):
    """A parent or guardian as a person (a contact record; no login yet). One person can have several children.

    Consent to WhatsApp messages belongs to the person. It is `unknown` until the consent feature records an
    answer; main_app.whatsapp.send_whatsapp refuses to send unless it is exactly `yes`.
    """
    CONSENT_UNKNOWN, CONSENT_YES, CONSENT_NO = 'unknown', 'yes', 'no'
    CONSENT = [(CONSENT_UNKNOWN, 'Unknown'), (CONSENT_YES, 'Yes'), (CONSENT_NO, 'No')]

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=13)  # +91XXXXXXXXXX, unique per college
    email = models.EmailField(blank=True, default="")
    whatsapp_consent = models.CharField(max_length=7, choices=CONSENT, default=CONSENT_UNKNOWN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['phone'], name='uniq_guardian_phone')]

    def __str__(self):
        return 'Guardian #%s' % self.pk


class StudentGuardian(models.Model):
    """Links a student to a guardian (siblings share one guardian)."""
    FATHER, MOTHER, GUARDIAN, OTHER = 'father', 'mother', 'guardian', 'other'
    RELATION = [(FATHER, 'Father'), (MOTHER, 'Mother'), (GUARDIAN, 'Guardian'), (OTHER, 'Other')]

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE)
    relation = models.CharField(max_length=10, choices=RELATION)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['student', 'guardian'], name='uniq_student_guardian'),
            models.UniqueConstraint(fields=['student'], condition=models.Q(is_primary=True),
                                    name='one_primary_guardian_per_student'),
        ]

    def __str__(self):
        return 'Link #%s' % self.pk


class StudentResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    offering = models.ForeignKey('academics.SubjectOffering', on_delete=models.PROTECT)
    test = models.FloatField(default=0)
    exam = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, raw=False, **kwargs):
    """Give every new user the profile row matching their role."""
    if raw or not created:
        return
    profile_model = {CustomUser.COLLEGE_ADMIN: Admin, CustomUser.STAFF: Staff, CustomUser.STUDENT: Student}.get(instance.user_type)
    if profile_model is not None:
        profile_model.objects.get_or_create(admin=instance)
# todos
