FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy source code
COPY . .

# Install dependencies (if a requirements.txt exists; ignore errors otherwise)
RUN pip install --no-cache-dir --upgrade pip && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; else echo "No requirements.txt, skipping"; fi

# Environment variables (mirroring compose)
ENV PYTHONUNBUFFERED=1
ENV HOST_PORT=8765
ENV HOST_BIND=0.0.0.0

# Expose the service port
EXPOSE 8765

# Default command to start the host application
CMD ["python", "app.py"]
