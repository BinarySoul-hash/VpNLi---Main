FROM python:3.11-slim

WORKDIR /app

# System deps for Pillow / qrcode
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev libssl-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

CMD ["python", "bot.py"]
