FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb dbus dbus-x11 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir playwright httpx flask
RUN playwright install chromium --with-deps

WORKDIR /app
COPY app.py .
RUN mkdir -p /data/output

EXPOSE 10000
CMD ["python", "app.py"]
