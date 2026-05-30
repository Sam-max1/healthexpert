# HealthExpert: Installation & Configuration Guide

HealthExpert is designed as an **offline-first, zero-token-cost** agentic orchestration platform. This guide covers deployment across various hardware footprints, ensuring 100% data sovereignty without requiring internet access.

---

## 1. Deployment Tiers & Hardware Requirements

HealthExpert scales dynamically based on available hardware, adapting its LLM inference backend while maintaining the same CrewAI orchestration logic.

| Deployment Tier | Target Hardware | Minimum Specs | AI Backend | Typical Use Case |
|---|---|---|---|---|
| **Tier 1: Mobile / Handheld** | iOS, Android, Tablets | 4GB RAM, NPU/Neural Engine | ExecuTorch / MLC-LLM | Traveling users, in-flight, remote field workers |
| **Tier 2: Edge / IoT** | Raspberry Pi 5, Hailo-10H, Jetson Nano | 8GB RAM, Edge NPU | llama.cpp (GGUF) | Rural clinics, disaster response command centers |
| **Tier 3: Desktop / Workstation** | Windows/Linux PC, Mac M-Series | 16GB RAM, Dedicated GPU | transformers / MLX | Small organizations, schools, legal/policy offices |

---

## 2. Tier 3: Desktop / Workstation Installation

The Desktop deployment offers the highest reasoning capability using the `Qwen3-8B` model quantized to 4-bit NF4.

### Prerequisites (Offline Prep)
Before traveling to an air-gapped or low-internet environment, you must download the offline installers:
1. Download the HealthExpert Docker Image bundle (`healthexpert_v2.tar`).
2. Download the HuggingFace model cache directory (`~/.cache/huggingface`).

### Offline Installation Steps
1. **Load Docker Image:**
   ```bash
   docker load -i healthexpert_v2.tar
   ```
2. **Mount Model Cache & Run:**
   ```bash
   docker run -d \
     --name healthexpert \
     -p 5050:5050 \
     -p 8002:8002 \
     -v ~/.cache/huggingface:/root/.cache/huggingface \
     healthexpert:latest
   ```
3. **Access the Application:** Open `http://localhost:5050` in your web browser.

---

## 3. Tier 2: Edge / Single-Board Installation (llama.cpp)

For devices like the Raspberry Pi 5, standard `transformers` are too heavy. HealthExpert utilizes `llama.cpp` for CPU/NPU bound inference.

### Setup
1. Transfer the highly quantized Small Language Model (SLM), e.g., `Phi-3-mini-4k-instruct-q4_k_m.gguf`, to the Edge device via USB.
2. Configure `config.py` to point the `LLM_BASE_URL` to the local llama.cpp server.
3. **Launch Inference Server:**
   ```bash
   ./server -m models/Phi-3-mini-4k-instruct-q4_k_m.gguf -c 4096 --host 0.0.0.0 --port 8002
   ```
4. **Start HealthExpert App:**
   ```bash
   python app.py
   ```

---

## 4. Tier 1: Mobile Deployment (React Native + MLC-LLM)

*Note: The mobile application is distributed via MDM (Mobile Device Management) or side-loading for enterprise clients, and via App Stores for B2C users.*

### Configuration
1. **Model Download:** Upon first launch (while connected to Wi-Fi), the app will download a 2-bit or 4-bit quantized SLM (approx. 1.5GB - 2.5GB) directly to the device storage.
2. **Knowledge Base Sync:** Foundation policy databases are synchronized.
3. **Offline Mode:** Once downloaded, the app automatically switches to Offline Mode. The embedded `MLC-LLM` engine utilizes the device's native NPU (e.g., Apple Neural Engine) to execute agentic workflows with zero battery-draining cloud pings.

---

## 5. Network Configuration & Security

- **Zero-Trust by Default:** HealthExpert does not require inbound or outbound internet connections to function.
- **Localhost Binding:** By default, all internal microservices (`gen_llm` on 8002, `embed_llm` on 8003) bind to `127.0.0.1`, ensuring they cannot be accessed by other devices on a shared Wi-Fi network (e.g., public airplane Wi-Fi).
- **Encrypted Storage:** The embedded ChromaDB vector store data is fully encrypted using AES-256 via the `security.py` module.
