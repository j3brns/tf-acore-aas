FROM python:3.12-slim

WORKDIR /app

# Install dependencies separately for better layer caching
COPY tests/mocks/mock_jwks/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY tests/mocks/mock_jwks/main.py main.py

EXPOSE 8766

HEALTHCHECK --interval=5s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8766/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
