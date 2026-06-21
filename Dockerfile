FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg gcc g++ --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]
