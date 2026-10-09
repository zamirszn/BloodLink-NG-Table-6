FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Build-time only values so collectstatic can import settings.
RUN SECRET_KEY=build DATABASE_URL=sqlite:///build.db ALLOW_LOCMEM_CACHE=1 python manage.py collectstatic --noinput
RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
