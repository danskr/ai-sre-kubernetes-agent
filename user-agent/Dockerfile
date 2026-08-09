FROM python:3.12-slim

WORKDIR /opt/user-agent

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 useragent \
    && chown -R useragent:useragent /opt/user-agent

USER 10001

CMD ["python", "-m", "app.main"]
