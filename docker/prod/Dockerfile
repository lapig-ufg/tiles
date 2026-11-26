FROM python:3.12-slim-bookworm

# A montagem de /etc/localtime deve atingir um arquivo, sem substituir Etc/UTC.
RUN cp /etc/localtime /tmp/container-localtime && \
    rm /etc/localtime && mv /tmp/container-localtime /etc/localtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv


ENV TILES_ENV=production
    
LABEL maintainer="Jario Matos da Rocha <jairo@jairomr.com.br>"

WORKDIR /app

COPY . .

RUN apt-get update && apt-get install --no-install-recommends -y curl build-essential git nano htop && \
    uv pip install --system --no-cache-dir -r requirements.txt



