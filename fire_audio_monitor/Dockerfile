ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends alsa-utils libasound2-plugins portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy requests sounddevice

WORKDIR /app
COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

STOPSIGNAL SIGTERM
CMD ["/run.sh"]
