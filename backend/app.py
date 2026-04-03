"""
Marriage Calculator – Flask application entry point.
"""

from flask import Flask, send_from_directory
from flask_cors import CORS
import os

from database import init_db
from routes import api


def create_app() -> Flask:
    app = Flask(__name__, static_folder="../frontend", static_url_path="")

    allowed_origins = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5000,https://ahtsisab.github.io",
    ).split(",")
    CORS(app, supports_credentials=True, origins=[o.strip() for o in allowed_origins])

    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

    # Cross-origin cookie config (GitHub Pages → Railway)
    app.config["SESSION_COOKIE_SAMESITE"] = "None"
    app.config["SESSION_COOKIE_SECURE"]   = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    init_db()
    app.register_blueprint(api)

    @app.get("/")
    def serve_index():
        return send_from_directory(app.static_folder, "index.html")

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=True)
