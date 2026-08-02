# Models

AI weights are deliberately kept out of Git. Install public weights and a separately hosted custom PPE classifier by running:

```powershell
.\scripts\download_models.ps1
```

For the custom classifier, set `SENTRYLAB_PPE_MODEL_URL` first. Set `SENTRYLAB_MODEL_TOKEN` as well when the storage provider accepts a bearer token. Neither value is written to disk by the script.

Expected production layout:

```

The dashboard reads `/api/models/status` to validate this layout. A file must exist and contain data to be considered installed. The validator never loads the models into memory.
models/
  restricted_zone/yolo11n-pose.pt
  unsafe_proximity/
    yolo11n.pt
    config.json
    preprocessor_config.json
    model.safetensors
  ppe/
    yolov8n.pt
    ppe_multilabel_best.pt
```
