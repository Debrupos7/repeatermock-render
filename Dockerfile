FROM python:3.12-slim

RUN pip install --no-cache-dir httpx flask

WORKDIR /app
COPY app.py .
RUN mkdir -p /data/output

EXPOSE 10000
CMD ["python", "app.py"]
