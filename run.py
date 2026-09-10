#!/usr/bin/env python3
"""
Standalone launcher for the Research Notes app.

Usage:
    python run.py

The port (and a few other settings) are read from a .env file in this
directory, or from the environment if you prefer to set them that way.
See .env.example for the available options.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402  (import after load_dotenv on purpose)

if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"Research Notes starting on http://{host}:{port}  (Ctrl+C to stop)")
    app.run(host=host, port=port, debug=debug)
