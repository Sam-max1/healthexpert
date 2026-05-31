FROM continuumio/miniconda3:latest

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# METHOD 2: Create the Conda environment and permanently update the PATH
RUN conda create -n docai python=3.12.12 -y
ENV PATH="/opt/conda/envs/docai/bin:$PATH"

# CRITICAL FIX: Install CPU-only PyTorch first to prevent downloading 2.5GB of useless CUDA drivers
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Copy requirements and install
COPY requirements.txt .

# Remove the 'torch' line from requirements dynamically so we don't overwrite our CPU version[cite: 3], and remove GPU-only bitsandbytes
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/bitsandbytes/d' requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

# Copy application codebase
COPY . .

# Ensure the start script is executable [cite: 4]
RUN chmod +x start.sh

# Expose default HuggingFace Spaces port
ENV PORT=7860
EXPOSE 7860

# Run all microservices together
ENTRYPOINT ["bash", "start.sh"]
