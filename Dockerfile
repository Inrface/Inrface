FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/Inrface/Inrface" \
      org.opencontainers.image.description="Inrface: a self-hosted idea inbox that people and their AI apps scope together" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --no-create-home app && mkdir /data && chown app /data
COPY innerface ./innerface
COPY docs ./docs
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"
CMD ["uvicorn", "innerface.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
