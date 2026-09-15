# syntax=docker/dockerfile:1
FROM python:3.12-slim

# libgl1/libglib2.0-0: runtime deps some opencv/ultralytics codepaths still
# dlopen even with the "headless" opencv build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
