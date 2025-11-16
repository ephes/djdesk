WebSocket Channels
==================

Document live updates consumed by the Electron renderer (dataset sync queue, task
progress, notification indicators).

Include:

- Channel names (e.g., ``ws://.../ws/datasets``)
- Payload schema for progress updates (0–100%, ETA, log excerpt)
- Subscription rules (auth, feature flags)
