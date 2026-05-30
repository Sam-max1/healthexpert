# HealthExpert: Administrator Guide

This guide is intended for IT administrators managing HealthExpert deployments across standalone users, edge devices, and enterprise fleets operating in air-gapped or disconnected environments.

---

## 1. Fleet Management & Offline Synchronization

Managing AI deployments without internet access requires a robust synchronization strategy. HealthExpert uses a **Sync-Package** architecture.

### Creating an Offline Sync-Package
When new corporate policies, healthcare guidelines, or curriculum updates are released, administrators can package them for offline distribution:
1. **Ingest locally:** Ingest the new documents into the Admin Master instance of HealthExpert.
2. **Export Vector DB:** Export the updated ChromaDB foundation tier.
   ```bash
   python manage_db.py export --tier foundation --output update_v2.pkg
   ```
3. **Distribute:** Distribute `update_v2.pkg` to remote sites via USB drive, secure intranet drop, or MDM (Mobile Device Management) push.

### Applying an Offline Sync-Package
On the remote device (Edge node or Mobile app):
1. Navigate to the Admin Dashboard (`http://localhost:5050/admin`).
2. Upload the `update_v2.pkg` file.
3. The system will seamlessly merge the new vector embeddings and graph relations without requiring a cloud connection.

---

## 2. Managing Tiered Knowledge Bases

HealthExpert divides knowledge into two strictly isolated tiers to prevent data contamination:

- **Foundation Tier:** Managed strictly by Administrators. Contains verified, global knowledge (e.g., official healthcare policies, company-wide HR docs, school curriculums). Read-only for standard users.
- **Extended Tier:** Managed by the End User. Contains user-specific, temporary, or experimental documents.

### Admin Controls
- Admins can wipe the Extended Tier globally via the **Purge DB** button in the UI, ensuring sensitive session data is removed between users (e.g., in a shared school or clinic setting).
- Admins can enforce an **Ephemeral Mode** where the Extended Tier is stored entirely in RAM and deleted upon app closure.

---

## 3. Hardware & Telemetry Monitoring

In offline environments, monitoring hardware limits (battery, VRAM, thermal throttling) is critical.

### Viewing Telemetry
The Admin Dashboard provides real-time insights into the device's operational status:
- **NPU/GPU Utilization:** Ensures the embedded LLM is not falling back to the CPU (which drains battery rapidly).
- **VRAM / RAM Budgeting:** Displays current memory usage. If memory exceeds 90%, the Admin can configure the app to aggressively offload older context from the KV Cache.
- **Inference Speed (Tokens/sec):** Monitors thermal throttling. If tokens/sec drops significantly, it indicates the device is overheating.

### The Kill Switch
In the event of a runaway CrewAI agent loop or extreme thermal event on an edge device, Admins can activate the **Emergency Kill Switch** via the UI or API (`POST /api/admin/kill`). This instantly terminates the LLM subprocesses and flushes VRAM.
