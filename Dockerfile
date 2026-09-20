FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better build caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Environment variables (mirroring compose)
ENV PYTHONUNBUFFERED=1
ENV HOST_PORT=8765
ENV HOST_BIND=0.0.0.0

# Expose the service port
EXPOSE 8765

# Default command to start the host application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
