FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
ARG CACHEBUST=2026-05-05
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "start.py"]
