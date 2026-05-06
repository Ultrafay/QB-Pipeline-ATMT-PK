FROM python:3.11-slim-bookworm

# poppler-utils is required by pdf2image to convert PDF pages to images
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads static credentials

CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
