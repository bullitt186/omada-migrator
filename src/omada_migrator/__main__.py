"""Entry point: python -m omada_migrator (FR-12)."""

import logging
from pathlib import Path

import uvicorn

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def main():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "omada-migrator.log"),
        ],
    )
    uvicorn.run(
        "omada_migrator.app:app",
        host="127.0.0.1",
        port=8888,
        reload=True,
        reload_excludes=["logs/*", "snapshots/*", ".codegraph/*"],
        log_level="info",
    )


if __name__ == "__main__":
    main()
