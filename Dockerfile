# Plumb: one container serving the API, the React app and the local models.
# Built for a Hugging Face Docker Space (port 7860, runs as uid 1000).

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/hf TOKENIZERS_PARALLELISM=false \
    PLUMB_DATA_DIR=/tmp/plumb-data PLUMB_STATIC_DIR=/app/frontend/dist
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r backend/requirements.txt
# Bake the local models into the image so the first check run never waits on a download.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
from transformers import AutoTokenizer, AutoModelForSequenceClassification as M; AutoTokenizer.from_pretrained('cross-encoder/nli-deberta-v3-small'); M.from_pretrained('cross-encoder/nli-deberta-v3-small')"
COPY backend/ backend/
COPY demo/ demo/
COPY --from=web /web/dist frontend/dist
RUN python backend/scripts/make_demo_files.py /app/demo \
 && useradd -m -u 1000 user && chown -R user:user /app
USER user
EXPOSE 7860
WORKDIR /app/backend
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/api/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
