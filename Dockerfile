FROM python:3.12-alpine
WORKDIR /app
COPY deploy_pwa/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY deploy_pwa/ /app/deploy_pwa/
EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 deploy_pwa.server:app"]
