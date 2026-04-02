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
    CORS(app)

    # Initialize DB schema on startup
    init_db()

    # Register API blueprint
    app.register_blueprint(api)

    # Serve the frontend SPA for any non-API route
    @app.get("/")
    def serve_index():
        return send_from_directory(app.static_folder, "index.html")

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=True)
