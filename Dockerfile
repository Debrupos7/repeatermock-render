FROM python:3.12-slim

# Install Chromium + dbus + Xvfb + all deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium xvfb dbus dbus-x11 \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 fonts-liberation \
    libx11-xcb1 libxcb-dri3-0 libxss1 libgtk-3-0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python deps
RUN pip install --no-cache-dir nodriver httpx flask

WORKDIR /app
COPY app.py .

RUN mkdir -p /data/output

# Set CHROME_PATH to chromium
ENV CHROME_PATH=/usr/bin/chromium

EXPOSE 10000

CMD ["python", "app.py"]
