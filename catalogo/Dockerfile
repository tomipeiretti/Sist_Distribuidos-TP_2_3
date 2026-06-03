FROM python:3.11-slim

WORKDIR /app

# Cache de capas: dependencias antes que el codigo.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Generar stubs como root (necesita escribir en /app).
COPY catalogo.proto .
RUN python -m grpc_tools.protoc --python_out=. --grpc_python_out=. -I. catalogo.proto

# Usuario no-root (rubrica E1).
RUN useradd -m -u 1000 appuser
USER appuser

COPY --chown=appuser:appuser main.py .

# K8s usa esto + el livenessProbe para detectar zombies.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import socket; socket.create_connection(('localhost', 50051), 1)" || exit 1

EXPOSE 50051
CMD ["python", "main.py"]
