"""Start the streaming HTTP API for the LangGraph debate system."""

import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

from src.debate_api import (
    DebateJobManager,
    ProductionDebateRunner,
    build_debate_api_app,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    runner = ProductionDebateRunner.from_env()
    manager = DebateJobManager(
        runner,
        max_workers=int(os.getenv("DEBATE_API_MAX_WORKERS", "1")),
    )
    origins = [
        item.strip()
        for item in os.getenv(
            "DEBATE_API_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
        if item.strip()
    ]
    app = build_debate_api_app(manager, allowed_origins=origins)
    try:
        uvicorn.run(
            app,
            host=os.getenv("DEBATE_API_HOST", "127.0.0.1"),
            port=int(os.getenv("DEBATE_API_PORT", "8000")),
        )
    finally:
        manager.close()


if __name__ == "__main__":
    main()
