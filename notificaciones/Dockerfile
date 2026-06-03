FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m -u 1000 appuser
USER appuser

COPY --chown=appuser:appuser worker.py .

# El consumer no expone puerto: verificamos que el modulo pika importe
# (proxy razonable de "el runtime esta vivo"). En K8s ademas hay livenessProbe
# tipo exec sobre el mismo principio.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import pika" || exit 1

CMD ["python", "worker.py"]
