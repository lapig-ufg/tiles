FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv


ENV TILES_ENV=production
ENV NEW_RELIC_CONFIG_FILE=newrelic.ini
    
LABEL maintainer="Jario Matos da Rocha <jairo@jairomr.com.br>"

WORKDIR /app

COPY . .

RUN apt-get update && apt-get install --no-install-recommends -y curl build-essential git nano htop && \
    uv pip install --system --no-cache-dir -r requirements.txt

ENTRYPOINT ["newrelic-admin", "run-program"]



