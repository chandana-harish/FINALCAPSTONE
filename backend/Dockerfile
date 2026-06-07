# --- FastAPI Backend Dockerfile ---
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any are needed in the future
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend source code
COPY backend/ ./backend/

# Expose port 8000 for FastAPI
EXPOSE 8000

# Set environment variable to make imports clean
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Start the FastAPI application with Uvicorn
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
