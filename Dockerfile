# Image for staging/production (Coolify "Dockerfile" build pack). One process: gunicorn.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time. These values exist only for this one command; the running container gets
# its real secret and hosts from its environment, never from the image.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS=build.invalid \
    RECAPTCHA_SITE_KEY=x RECAPTCHA_SECRET_KEY=x python manage.py collectstatic --noinput

RUN useradd --create-home app && mkdir -p /app/media && chown -R app /app/media
USER app

EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "college_management_system.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
