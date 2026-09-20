FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Tonarr-Audio/Host"
LABEL org.opencontainers.image.description="Tonarr Host - Dedicated High-Performance Music & Metadata Server"
LABEL org.opencontainers.image.licenses="MIT"

# Set working directory
WORKDIR /app

# Ensure data and music mount points exist
RUN mkdir -p /data /music

# Copy requirements first for better build caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code and web dashboard
COPY . .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV HOST_PORT=8765
ENV HOST_BIND=0.0.0.0
ENV HOST_DATA_DIR=/data

# Persistent data and music volumes
VOLUME ["/data", "/music"]

# Expose the service port
EXPOSE 8765

# Default command to start the Tonarr Host application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
