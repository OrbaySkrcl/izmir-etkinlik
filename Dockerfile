# İzmir Etkinlik Botu — üretim imajı
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Istanbul

WORKDIR /app

# lxml ve asyncpg için gerekli sistem kütüphaneleri
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxml2 libxslt1.1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Bağımlılıklar önce kopyalanır: kod değişince katman önbelleği korunur.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config ./config

# Paket site-packages'a kurulduğu için varsayılan depo-göreli yol çözülmez;
# kaynak dosyasının konumunu açıkça bildiriyoruz.
ENV SOURCES_FILE=/app/config/sources.yaml

# SQLite kullanılıyorsa veritabanı buraya yazılır (Railway volume mount noktası).
RUN mkdir -p /app/data

# Kök olmayan kullanıcı
RUN useradd --create-home --uid 10001 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["izmir-etkinlik", "serve", "--json-logs"]
