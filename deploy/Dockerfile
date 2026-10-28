FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY heroez_ops ./heroez_ops
RUN pip install --no-cache-dir .
COPY config ./config
ENV PYTHONUNBUFFERED=1 HEROEZ_CONFIG=/app/config/config.yaml HEROEZ_DB=/app/data/heroez.db
EXPOSE 8000
CMD ["uvicorn", "heroez_ops.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
