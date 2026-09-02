FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
VOLUME ["/config", "/state"]
ENV S2N_SYNC_STATE_DIR=/state
ENTRYPOINT ["python", "/app/__main__.py", "-c", "/config/config.yaml"]
CMD ["sync"]
