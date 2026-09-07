FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends libimage-exiftool-perl libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --uid 10001 --create-home catalog && mkdir -p /data/cache && chown -R catalog:catalog /data
USER catalog
EXPOSE 8000
CMD ["sh", "-c", "python -c 'from app.db import init_db; init_db()' && exec gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 120 'app.web:create_app()'"]
