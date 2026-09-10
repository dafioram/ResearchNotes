import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()

    app = Flask(__name__)

    base_dir = Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get("DATA_DIR", base_dir / "data"))

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(16),
        DATABASE_PATH=str(data_dir / "notes.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,  # 50 MB per upload
    )

    if test_config:
        app.config.update(test_config)

    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    from . import db
    db.init_db(app)

    from . import routes
    app.register_blueprint(routes.bp)

    return app
