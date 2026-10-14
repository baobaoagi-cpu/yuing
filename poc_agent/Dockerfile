FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY poc_agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY poc_agent/agent.py ./agent.py
COPY poc_agent/signed_context.py ./signed_context.py
COPY poc_agent/call_usage_meter.py ./call_usage_meter.py
COPY poc_agent/tts_config.py ./tts_config.py
COPY poc_agent/emotion_policy.py ./emotion_policy.py
COPY poc_agent/web_search.py ./web_search.py
COPY poc_agent/persona/chen_dunni_constitution_v1.md ./persona/chen_dunni_constitution_v1.md
COPY poc_agent/persona/pset_principles_v0.1.md ./persona/pset_principles_v0.1.md
COPY persona/innerstellar_style_v0.1.md ./persona/innerstellar_style_v0.1.md

CMD ["python", "agent.py", "start"]
