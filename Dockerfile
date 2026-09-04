FROM python:3.12-slim

# Install system deps + dbus + xvfb
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb dbus dbus-x11 \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 fonts-liberation \
    libx11-xcb1 libxss1 libgtk-3-0 libdbus-1-3 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python deps + Playwright + nodriver
RUN pip install --no-cache-dir nodriver httpx flask playwright
# Install Playwright's Chromium + its deps
RUN playwright install chromium --with-deps

WORKDIR /app
COPY app.py .
RUN mkdir -p /data/output

EXPOSE 10000
CMD ["python", "app.py"]
