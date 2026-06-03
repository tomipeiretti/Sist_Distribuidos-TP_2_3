FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Generar stubs antes de bajar de privilegios.
COPY catalogo.proto .
RUN python -m grpc_tools.protoc --python_out=. --grpc_python_out=. -I. catalogo.proto

RUN useradd -m -u 1000 appuser
USER appuser

COPY --chown=appuser:appuser main.py logging_setup.py ./

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
