"""`python -m dealfinder`: serve the dashboard on http://127.0.0.1:8000/."""
from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from dealfinder.config import load_config
from dealfinder.web.app import create_app

CONFIG_PATH = Path("config.yaml")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(CONFIG_PATH if CONFIG_PATH.exists() else None)
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
