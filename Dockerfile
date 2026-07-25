FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download models at build time (eliminates cold start for users)
RUN python -c "
from transformers import pipeline
print('Downloading reranker model...')
pipeline('text-classification', model='BAAI/bge-reranker-base')
print('✓ Reranker model cached.')
"

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
