# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# SigLIP (weights/classifier.json, "type": "ensemble") — веса скачиваются при
# сборке, чтобы воркеру в рантайме не нужен был интернет.
ENV HF_HOME=/opt/hf
RUN python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-16-SigLIP', pretrained='webli')" \
    && chmod -R a+rX /opt/hf

COPY . .

CMD ["python", "worker.py"]
