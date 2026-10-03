"""Serve a demo mock endpoint until the gateway and project workers are added."""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

app = FastAPI(title="Reqlica Mock Server")


@app.get("/mock/demo/hello", tags=["Demo"])
def hello() -> dict[str, str]:
    """Return a sample response for checking the mock server connection."""
    return {"message": "Hello from Reqlica", "project_id": "demo"}


def main() -> None:
    """Start the mock server using settings from the repository's .env file."""
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    host = os.environ.get("MOCK_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCK_PORT", "4000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
