FROM python:3.11-slim

WORKDIR /app

# Derleyici araçları ve healthcheck için curl kurulumu
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Bağımlılıkları yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını kopyala
COPY . .

# Port tanımı
EXPOSE 8000

# Docker Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Servisi başlat
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
