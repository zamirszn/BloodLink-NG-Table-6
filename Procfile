release: python manage.py migrate --noinput
web: gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --access-logfile -
worker: celery -A config worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-4}
