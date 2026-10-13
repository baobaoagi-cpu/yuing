FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY poc_agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY poc_agent/agent.py ./agent.py
COPY poc_agent/persona/chen_dunni_constitution_v1.md ./persona/chen_dunni_constitution_v1.md

CMD ["python", "agent.py", "start"]
