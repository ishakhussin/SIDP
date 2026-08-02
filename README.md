# SentryLab

AI-powered laboratory safety monitoring for restricted-zone entry, unsafe proximity, and personal protective equipment (PPE) compliance.

SentryLab is a local Flask application that connects to USB and RTSP cameras, runs multiple safety detectors, displays annotated live feeds, and records evidence for confirmed unsafe incidents. It is designed for a single operator running the system manually on one computer.

## Safety monitoring

- **Restricted Zone:** detects entry using ankle keypoints and camera-specific polygons.
- **Unsafe Proximity:** estimates the distance between tracked people and warns below 1.5 metres.
- **PPE Compliance:** checks each detected person for a lab coat, mask, and gloves.
- **Majority voting:** shows WARNING immediately, then confirms UNSAFE when at least three valid violation samples are observed within five one-second checks. UNKNOWN samples are excluded.
- **Incident evidence:** creates one annotated ten-second clip for each UNSAFE incident, covering three seconds before and seven seconds after confirmation.
- **Monitoring heartbeat:** records a concise SAFE status every five minutes when the system is operating without an incident.

## Dashboard

- Live multi-camera monitoring
- Independent detector controls for every camera
- AI overlay toggle without stopping detection
- Live restricted-zone polygon editor
- SAFE, WARNING, and UNSAFE status display
- Event filtering and inline video playback
- CSV and ZIP export
- Manual incident deletion
- Camera status and automatic reconnection

## Architecture

```text
app.py                         Thin application launcher
config/                        Camera configuration
models/                        Local AI model files (not stored in Git)
sentrylab/
  api/                         Flask routes and JSON APIs
  cameras/                     Camera ownership, capture, and reconnection
  database/                    SQLite schema and repositories
  detection/                   Restricted-zone, proximity, and PPE models
  domain/                      Shared detection data contracts
  services/                    Voting, incidents, evidence, and heartbeats
  streaming/                   Snapshot and MJPEG streaming
static/                        Dashboard JavaScript and styles
templates/                     Dashboard, overview, and event pages
tests/                         Automated test suite
```

`app.py` only creates the application, starts the runtime services, and launches Flask. Camera, AI, voting, incident, and recording logic remain in separate modules so that failures are easier to isolate and test.

## Requirements

- Windows 10 or Windows 11
- Python 3.12 recommended
- USB camera or RTSP-compatible IP camera
- NVIDIA CUDA-capable GPU recommended for smooth multi-model inference
- FFmpeg recommended for browser-compatible H.264 evidence clips

## Installation

Open PowerShell in the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## AI models

Model weights are not committed to GitHub because they are large and may have separate licences. Place them in the following layout:

```text
models/
  restricted_zone/
    yolo11n-pose.pt
  unsafe_proximity/
    yolo11n.pt
    config.json
    preprocessor_config.json
    model.safetensors
  ppe/
    yolov8n.pt
    ppe_multilabel_best.pt
```

See [models/README.md](models/README.md) for the expected production layout.

After installing the Python requirements, download the public models with:

```powershell
.\scripts\download_models.ps1
```

The PPE classifier is a custom trained model and has no public default URL. The model owner can provide a private or public download address without putting credentials in Git:

```powershell
$env:SENTRYLAB_PPE_MODEL_URL = "https://your-model-storage.example/ppe_multilabel_best.pt"
$env:SENTRYLAB_MODEL_TOKEN = "optional-private-storage-token"
.\scripts\download_models.ps1
```

The token exists only in the current PowerShell environment and is never saved by the script. A partial download is not accepted as an installed model.

Model availability can be checked without loading PyTorch at [http://127.0.0.1:5000/api/models/status](http://127.0.0.1:5000/api/models/status). The dashboard displays `MODEL MISSING` when an enabled detector does not have all its files. The web application and automated tests can run without model weights, but live AI inference cannot.

## Camera configuration

Edit `config/cameras.json` to configure USB cameras and enable or disable camera slots.

### Tapo CAM 01

CAM 01 uses the Tapo RTSP stream. Before starting:

1. In the Tapo mobile app, open the camera settings and create a **Camera Account** under Advanced Settings. This is different from the normal TP-Link account.
2. Find the camera's local IP address under Device Info. The camera and SentryLab computer must be on the same local network.
3. Test the high-quality stream without saving or displaying the password:

```powershell
.\scripts\setup_tapo.ps1 -TestOnly
```

4. When the test reports `CAM 01 ONLINE`, start SentryLab through the same secure launcher:

```powershell
.\scripts\setup_tapo.ps1
```

The launcher asks for the camera IP address, Camera Account username, and password. It URL-encodes special characters, uses RTSP over TCP, verifies that a real frame arrives, and then starts the application. Credentials exist only in that PowerShell process and are never written into the repository or printed.

Use the lower-resolution Tapo stream when the network or AI pipeline needs less load:

```powershell
.\scripts\setup_tapo.ps1 -Stream 2
```

Advanced users can provide the RTSP address directly through an environment variable:

```powershell
$env:SENTRYLAB_CAM01_RTSP_URL = "rtsp://username:password@camera-address:554/stream1"
```

TP-Link's official instructions are available in [How to View Tapo Camera Using RTSP/ONVIF](https://www.tp-link.com/us/support/faq/2680/).

### Camera controls

- **CAM 01 (Tapo C200):** digital zoom plus real ONVIF pan and tilt on port 2020.
- **CAM 02 (eMeet USB):** digital zoom only; it has no pan/tilt motor.
- Zoom is remembered separately for each camera in the browser. It changes the
  operator's view only; detection continues on the complete source frame.
- To calibrate a Tapo preset, move CAM 01 to the required position, click
  **Save Current**, then click **P1**, **P2**, or **P3**. A normal preset click
  moves the camera back to that saved position.

CAM 01 controls use the same temporary Camera Account credentials collected by
`scripts/setup_tapo.ps1`; credentials are not stored in the project. Install
`onvif-zeep` through `pip install -r requirements.txt` before using motor controls.

The included configuration uses:

- CAM 01: Tapo C200 through RTSP
- CAM 02: eMeet USB camera at 1920 x 1080, 30 FPS, MJPG
- CAM 03: disabled placeholder for future expansion

## Run SentryLab

```powershell
python app.py
```

On the Windows production laptop, `requirements.txt` installs the official
CUDA 12.8 PyTorch build so DepthPro, PPE, and YOLO can use the NVIDIA GPU.
Verify it after installation with:

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The expected result begins with `True`. A `False` result means depth processing
has fallen back to the CPU and will be substantially slower.

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in a browser. Stop the application with `Ctrl+C`.

## ESP32 audio alarm

Flash the firmware in [`firmware/sentrylab_audio_alarm`](firmware/sentrylab_audio_alarm), connect the ESP32 to the laptop by USB, and close Arduino Serial Monitor. SentryLab automatically selects a single connected CH340 ESP32 device. An explicit port can still override auto-detection:

```powershell
$env:SENTRYLAB_ALARM_COM_PORT = "COM5"
python app.py
```

To test the speaker independently of AI detection and the dashboard:

```powershell
python .\scripts\test_alarm.py --port COM5 --seconds 3
```

The laptop sends a command every second. The ESP32 loops `0001.mp3` while any Restricted Zone, Unsafe Proximity, or PPE subject has a confirmed `UNSAFE` level. The alarm continues when another use case remains unsafe and stops only after every use case has remained clear for two continuous seconds. A serial failure does not crash Flask; SentryLab reconnects automatically and exposes its state at `/api/alarm/status` and on the dashboard.

## Run the tests

```powershell
python -m unittest discover -s tests -v
```

The current release contains 75 automated tests covering cameras, streaming, voting, detectors, incident recording, dashboard APIs, and monitoring heartbeats.

## Runtime data and privacy

SQLite databases, evidence clips, logs, credentials, and AI weights are intentionally excluded from Git. Runtime files are created under `data/` on the local computer. Review recorded evidence before sharing it because it may contain identifiable people or private laboratory activity.

## Project status

SentryLab v123 is a production-oriented prototype undergoing physical-camera and real-world model validation. It should support laboratory safety personnel rather than replace trained human supervision or formal safety procedures.
