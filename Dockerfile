FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    tar \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Download and install MediaMTX (Linux AMD64)
RUN curl -L -o mediamtx.tar.gz https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_linux_amd64.tar.gz \
    && tar -xzf mediamtx.tar.gz -C /usr/local/bin/ mediamtx \
    && rm mediamtx.tar.gz

# Set up working directory
WORKDIR /app

# Copy requirements and adjust for headless environment
COPY requirements.txt .
RUN sed -i 's/opencv-python>=/opencv-python-headless>=/g' requirements.txt \
    && pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY . .

# Create directory structure and grant write permissions (for Hugging Face non-root user UID 1000)
RUN mkdir -p /app/Videos /app/outputs /app/scratch /app/.agents \
    && chmod -R 777 /app

# Expose port 8000
EXPOSE 8000

# Run lite_server.py on port 8000
CMD ["uvicorn", "lite_server:app", "--host", "0.0.0.0", "--port", "8000"]
