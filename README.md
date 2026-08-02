# SentryLab v123

Clean rebuild of the local laboratory safety monitoring application.

## Current milestone

- Application factory and thin launcher
- Central settings and clean camera numbering
- Health API
- Shared model-independent detection contract
- Five-vote WARNING/SAFE confirmation engine
- One-owner camera workers with latest-frame storage
- Automatic capture reconnection
- Camera status APIs that do not start camera connections
- Raw JPEG snapshots and MJPEG streams from the shared latest-frame slot
- Restricted Zone pose detection using ankle keypoints and bbox fallback
- Per-camera HOME/PTZ-preset polygons editable from the dashboard
- Immediate WARNING and five-second majority-vote UNSAFE confirmation
- Camera-level incidents with independent per-person state
- One annotated ten-second MP4 per UNSAFE incident (3s before, 7s after)
- Inline clip playback, CSV/ZIP export, and manual incident deletion
- No camera or AI model loads during Flask import
- Unsafe Proximity using tracked YOLO11 people plus DepthPro metric depth
- Responsive asynchronous depth inference with persistent 1.5 m pair overlays
- Independent pair voting with UNKNOWN samples excluded from confirmation

Run tests with:

```powershell
python -m unittest discover -s tests -v
```
