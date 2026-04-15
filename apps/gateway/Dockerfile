FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY apps/gateway/requirements.txt .
COPY artifacts/wheels/gateway /wheelhouse
RUN if [ -d /wheelhouse ] && [ "$(ls -A /wheelhouse 2>/dev/null)" ]; then \
      pip install --no-cache-dir --no-index --find-links=/wheelhouse -r requirements.txt ; \
    else \
      pip install --no-cache-dir -r requirements.txt ; \
    fi

COPY apps/gateway/app /app/app
COPY apps/gateway/static /app/static

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=3).read()" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
