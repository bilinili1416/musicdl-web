FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    MUSICDL_DATA_DIR=/data

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home musicdl
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py lyrics.py ./
COPY web/ ./web/
COPY deploy/entrypoint.sh /usr/local/bin/musicdl-entrypoint
RUN chmod +x /usr/local/bin/musicdl-entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/musicdl-entrypoint"]
CMD ["python", "server.py"]
