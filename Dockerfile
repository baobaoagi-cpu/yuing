FROM python:3.12-alpine
WORKDIR /app
COPY deploy_pwa/ /app/deploy_pwa/
EXPOSE 8080
CMD ["python", "/app/deploy_pwa/server.py"]
