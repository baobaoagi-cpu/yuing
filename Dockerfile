FROM python:3.12-alpine
WORKDIR /app
RUN pip install --no-cache-dir 'pypdf>=5,<7'
COPY deploy_pwa/ /app/deploy_pwa/
EXPOSE 8080
CMD ["python", "/app/deploy_pwa/server.py"]
