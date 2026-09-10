FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/uploads

ENV DATA_DIR=/app/data
ENV HOST=0.0.0.0
ENV PORT=5000

EXPOSE 5000

CMD ["python", "run.py"]
