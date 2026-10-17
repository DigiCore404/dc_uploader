FROM python:3.13

ARG UNRAR_VERSION=7.1.6
ARG RAR2FS_VERSION=1.29.7

# Add mediaarea repo and install its package
RUN apt-get update && \
    apt-get clean && \
    wget https://mediaarea.net/repo/deb/repo-mediaarea_1.0-26_all.deb && \
    dpkg -i repo-mediaarea_1.0-26_all.deb && \
    rm repo-mediaarea_1.0-26_all.deb && \
    wget https://download.opensuse.org/repositories/home:/movie_thumbnailer/Debian_13/amd64/mtn_3.5.0-1_amd64.deb && \
    apt-get install -y ./mtn_3.5.0-1_amd64.deb && \
    rm mtn_3.5.0-1_amd64.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    mediainfo \
    fuse3 \
    libfuse-dev \
    screen  \
    autoconf \
    mediainfo && \
    apt-get clean

# Install rar2fs
RUN wget https://github.com/hasse69/rar2fs/archive/refs/tags/v${RAR2FS_VERSION}.tar.gz && \
    tar zxvf v${RAR2FS_VERSION}.tar.gz && \
    cd rar2fs-${RAR2FS_VERSION} && \
    wget https://www.rarlab.com/rar/unrarsrc-${UNRAR_VERSION}.tar.gz && \
    tar zxvf unrarsrc-${UNRAR_VERSION}.tar.gz && \
    cd unrar && make --quiet lib && make install-lib && \
    cd .. && autoreconf -f -i && ./configure && make --quiet && make install && \
    cd .. && rm -r rar2fs-${RAR2FS_VERSION} && \
    sed -i 's/#user_allow_other/user_allow_other/g' /etc/fuse.conf

RUN mkdir -p /venv && \
    python3 -m venv /venv/dc_uploader && \
    /venv/dc_uploader/bin/pip install --upgrade pip wheel

COPY requirements.txt .

RUN /venv/dc_uploader/bin/pip install --no-cache-dir -r requirements.txt

# Set up environment
ENV PATH="/venv/dc_uploader/bin:$PATH"
WORKDIR /dc_uploader

# Copy application code
COPY . .

RUN /venv/dc_uploader/bin/python3 utils/database_utils.py initialize_all_databases && \
    find bin/mkbrr -type f -name "mkbrr" -exec chmod +x {} \; \
    && chmod +x scripts/*.sh; chmod +x utils/*.sh

WORKDIR /dc_uploader/scripts

ENTRYPOINT ["bash", "/dc_uploader/scripts/upload.sh", "--help"]