"""Configure the console's FastAPI application.

Register management API routes and serve the bundled dashboard from static/.
Mock API traffic and generated endpoint execution belong to reqlica.runtime.
"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

app = FastAPI(title="Console")


@app.get("/api/health", tags=["Console"])
def health() -> dict[str, str]:
    """Report that the console server is responding, not project worker health."""
    return {"status": "ok", "service": "reqlica-console"}


def main() -> None:
    """Start the console using settings from the repository's .env file."""
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    host = os.environ.get("CONSOLE_HOST", "127.0.0.1")
    port = int(os.environ.get("CONSOLE_PORT", "3000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
