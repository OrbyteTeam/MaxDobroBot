FROM python:3.13-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        chromium \
        chromium-driver \
        libglib2.0-0 \
        libnss3 \
        libgconf-2-4 \
        libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
COPY data/events.json ./
COPY cfg_parser.json ./
COPY cfg.json ./
COPY fsm_data.json ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot_main.py"]