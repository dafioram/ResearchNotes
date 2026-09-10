import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    data_dir = tmp_path / "data"
    application = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": str(data_dir / "notes.db"),
            "UPLOAD_DIR": str(data_dir / "uploads"),
            "SECRET_KEY": "test-secret",
        }
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()
