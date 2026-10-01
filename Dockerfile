FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
RUN pip install -e .
VOLUME ["/app/data", "/app/config"]
CMD ["sh", "-c", "urbex init-db && urbex run-bot"]
