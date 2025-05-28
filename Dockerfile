FROM python:3.13-slim

WORKDIR /app

# Install curl and tzdata, and set timezone to Asia/Shanghai
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl tzdata && \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

CMD ["python", "src/backup.py"]