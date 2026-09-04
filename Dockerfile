FROM python:3.12-slim

# Install Xvfb + dbus + deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb dbus dbus-x11 wget curl \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 fonts-liberation \
    libx11-xcb1 libxcb-dri3-0 libxss1 libgtk-3-0 libdbus-1-3 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir nodriver httpx flask playwright
RUN python -m playwright install chromium

# Find the Chrome binary and set it as env var
RUN CHROME_BIN=$(find /root/.cache/ms-playwright -name "chrome" -type f | head -1) && \
    echo "Found Chrome: $CHROME_BIN" && \
    echo "CHROME_PATH=$CHROME_BIN" > /app/chrome_path.env

WORKDIR /app
COPY app.py .
RUN mkdir -p /data/output

EXPOSE 10000
CMD ["sh", "-c", ". /app/chrome_path.env && python app.py"]
