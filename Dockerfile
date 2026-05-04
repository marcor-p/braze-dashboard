FROM python:3.12-slim

WORKDIR /app

# Install system deps for sqlite + curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (caching layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Railway sets $PORT — Flask reads it via os.environ
EXPOSE 8000

CMD ["python", "serve.py"]
