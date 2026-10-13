FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY poc_agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY poc_agent/agent.py ./agent.py

CMD ["python", "agent.py", "start"]
