ARG BASE_IMAGE=nvcr.io/nvidia/nemo:25.11
FROM ${BASE_IMAGE}

WORKDIR /app

COPY services/nemotron_asr/requirements.txt .
COPY artifacts/wheels/nemotron /wheelhouse
RUN if [ -d /wheelhouse ] && [ "$(ls -A /wheelhouse 2>/dev/null)" ]; then \
      pip install --no-cache-dir --no-index --find-links=/wheelhouse -r requirements.txt ; \
    else \
      pip install --no-cache-dir -r requirements.txt ; \
    fi

COPY services/nemotron_asr/app /app/app

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
  CMD python - <<'PY' || exit 1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=3).read()
PY

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
