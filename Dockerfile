FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

ENV PORT=8503
ENV CHECK_INTERVAL_DEFAULT=15
ENV MAX_FEEDS_FREE=50

EXPOSE 8503

CMD ["python", "server.py"]
