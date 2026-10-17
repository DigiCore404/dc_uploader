FROM ubuntu:22.04 AS mtn-tools
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends make gcc pkg-config curl ca-certificates libgd-dev libavutil-dev libavcodec-dev libavformat-dev libavfilter-dev libswscale-dev && rm -rf /var/lib/apt/lists/*
COPY scripts/build-mtn.sh /build-mtn.sh
RUN bash /build-mtn.sh /opt/mtn

FROM python:3.12-slim-bookworm AS archive-tools
RUN apt-get update && apt-get install -y --no-install-recommends build-essential autoconf automake libtool pkg-config libfuse-dev curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY scripts/build-archive-tools.sh /build-archive-tools.sh
RUN bash /build-archive-tools.sh /opt/archive

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg mediainfo fuse libfuse2 ca-certificates \
    && printf 'user_allow_other\n' > /etc/fuse.conf \
    && rm -rf /var/lib/apt/lists/*
COPY --from=archive-tools /opt/archive/bin/ /usr/local/bin/
COPY --from=archive-tools /opt/archive/share/ /usr/local/share/digitalcore-archive/
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY defaults.ini config.example.ini uploader.py backend.py web.py ./
COPY utils/ ./utils/
COPY webui/ ./webui/
COPY files/template.txt files/filters.json files/digitalcore-categories.json ./files/
COPY bin/mkbrr/ ./bin/mkbrr/
COPY bin/versions.json ./bin/versions.json
COPY scripts/install-mkbrr.py ./scripts/install-mkbrr.py
RUN python scripts/install-mkbrr.py
COPY bin/mtn/DejaVuSans.ttf bin/mtn/FONT-LICENSE.txt bin/mtn/COPYRIGHT.txt ./bin/mtn/
COPY --from=mtn-tools /opt/mtn/ /app/bin/mtn/
RUN find /app/bin -type f \( -name mkbrr -o -name mtn -o -name mtn.bin \) -exec chmod 755 {} + \
    && mkdir -p /config /content /watch /mounts /app/data /app/tmp /app/logs
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DIGITALCORE_CONTAINER=1 DIGITALCORE_CONFIG=/config/config.ini
EXPOSE 6080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6080/health', timeout=3)"
ENTRYPOINT ["python", "uploader.py"]
CMD ["web", "--host", "0.0.0.0", "--no-browser"]

FROM runtime AS test
ENV DIGITALCORE_CONFIG=/app/config.ini DIGITALCORE_CONTAINER=0 PYTHONPATH=/app:/app/tests
COPY config.seedbox.ini ./config.ini
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY install.sh start.sh start-web.sh docker-run.py SEEDBOX-INSTALL.md config.seedbox.ini requirements.txt compose.yaml compose.unrar.yaml .env.example setup.sh README-docker.md ./

FROM runtime AS release
