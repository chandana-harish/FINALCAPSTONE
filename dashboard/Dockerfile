# --- Streamlit Dashboard Dockerfile ---
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy dashboard code and backend code (dashboard imports backend.database / config)
COPY dashboard/ ./dashboard/
COPY backend/ ./backend/

# Expose port 8501 for Streamlit
EXPOSE 8501

# Set environment variable to make imports clean
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Configure Streamlit settings to disable CORS checks and run on port 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.enableCORS", "false"]
