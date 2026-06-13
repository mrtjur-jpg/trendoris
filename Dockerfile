FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY trendoris ./trendoris
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD uvicorn trendoris.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
