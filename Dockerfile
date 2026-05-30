FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application codebase
COPY . .

# Ensure the start script is executable
RUN chmod +x start.sh

# Expose default HuggingFace Spaces port
ENV PORT=7860
EXPOSE 7860

# Run all microservices together
ENTRYPOINT ["bash", "start.sh"]
