FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/sre-agent

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY langgraph.json ./langgraph.json

RUN useradd --create-home --uid 10001 agent \
    && chown -R 10001:10001 /opt/sre-agent
USER 10001

EXPOSE 8080 2024
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
