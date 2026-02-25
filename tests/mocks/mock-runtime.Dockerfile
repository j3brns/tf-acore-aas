FROM python:3.12-slim

WORKDIR /app

# Install dependencies separately for better layer caching
COPY tests/mocks/mock_runtime/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY tests/mocks/mock_runtime/main.py main.py

ENV LOG_LEVEL=INFO

EXPOSE 8765

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/ping')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
