# HealthExpert: User Guide

Welcome to HealthExpert, your completely private, offline, agentic AI assistant. HealthExpert requires zero internet connection to function and keeps all your data strictly on your device.

---

## 1. Getting Started

### The Interface
When you launch HealthExpert, you will see three main areas:
1. **The Ingestion Panel (Left):** Where you upload files.
2. **The Chat Panel (Middle):** Where you ask questions.
3. **The Output Panel (Right):** Where the Agent's detailed, cited answers appear.

### Ingesting Documents (No Cloud Required)
You can upload PDFs, Word Docs, Excel sheets, and even Images.
1. Drag and drop your files into the **Ingestion Panel**.
2. The Agent will securely process, chunk, and embed your documents locally using your device's processor.
3. **Privacy Note:** Because the system is 100% offline, you can safely upload highly sensitive documents (medical records, proprietary IP, personal finance) without fear of data leakage.

---

## 2. Asking Questions (The Agentic Workflow)

Unlike standard chatbots, HealthExpert uses an **Agentic Workflow**. When you ask a question, multiple specialized AI agents collaborate to find the answer:

1. **Type your question** in the Chat Panel.
2. **Retrieval:** The system first searches your uploaded documents (Vector Search) and understands connections (Graph Search).
3. **Gatekeeper:** A security agent ensures the retrieved information actually contains the answer to prevent hallucinations.
4. **Analysis:** The Analyst Agent synthesizes the information and provides a clear, Markdown-formatted answer with exact source citations (e.g., `[Source: policy.pdf]`).

*You will never incur a "Token Cost" or hit a "Usage Limit" because the AI runs directly on your hardware.*

---

## 3. Managing Battery and Performance (Mobile / Handheld Users)

Because HealthExpert runs powerful AI models directly on your device, it requires significant processing power. 

- **Expect Warmth:** It is normal for your device to get warm during long queries.
- **Battery Optimization:** If your battery falls below 20%, HealthExpert will automatically switch to "Economy Mode," relying more on fast Keyword Search (BM25) and less on deep Neural Embedding to conserve power.
- **Airplane Mode is Perfect:** The app functions identically whether you have 5G, Wi-Fi, or are completely disconnected in Airplane Mode.

---

## 4. Troubleshooting

- **"No Information Found" Message:** The Gatekeeper agent prevented the AI from guessing. If the answer isn't in the documents you uploaded, the AI will refuse to answer. Upload more relevant documents and try again.
- **Slow Responses:** If answers are taking longer than 20 seconds, ensure you don't have heavy background apps running, as the AI needs access to your device's RAM and processor.
