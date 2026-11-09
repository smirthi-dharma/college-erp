from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import *

# Register your models here.


class UserModel(UserAdmin):
    """The inherited UserAdmin still listed a `username` field this model does not have."""
    ordering = ('email',)
    list_display = ('email', 'first_name', 'last_name', 'user_type', 'is_active')
    search_fields = ('email', 'first_name', 'last_name')
    readonly_fields = ('last_login', 'date_joined')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'gender', 'address', 'profile_pic')}),
        ('Role and permissions', {'fields': ('user_type', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': ('email', 'password1', 'password2', 'user_type')}),)


admin.site.register(CustomUser, UserModel)
admin.site.register(Staff)
admin.site.register(Student)
admin.site.register(Course)
admin.site.register(Subject)
admin.site.register(Session)
