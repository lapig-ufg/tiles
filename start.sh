#/bin/bash

cd /app/tiles

git pull 
pip install --no-cache-dir -r requirements.txt 
gunicorn -k  uvicorn.workers.UvicornWorker --bind 0.0.0.0:8083 -w 4 -t 0 main:app"
