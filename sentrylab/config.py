"""Central paths and stable project settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_dir: Path
    data_dir: Path
    model_dir: Path
    config_dir: Path
    template_dir: Path
    static_dir: Path
    database_path: Path
    clips_dir: Path
    log_level: str = "INFO"
    alarm_serial_port: str | None = None
    alarm_baud_rate: int = 115200

    @classmethod
    def from_environment(cls) -> "Settings":
        project_dir = Path(__file__).resolve().parent.parent
        data_dir = project_dir / "data"
        return cls(
            project_dir=project_dir,
            data_dir=data_dir,
            model_dir=project_dir / "models",
            config_dir=project_dir / "config",
            template_dir=project_dir / "templates",
            static_dir=project_dir / "static",
            database_path=data_dir / "events.db",
            clips_dir=data_dir / "clips",
            log_level=os.getenv("SENTRYLAB_LOG_LEVEL", "INFO").upper(),
            alarm_serial_port=os.getenv("SENTRYLAB_ALARM_COM_PORT") or None,
            alarm_baud_rate=int(os.getenv("SENTRYLAB_ALARM_BAUD_RATE", "115200")),
        )
