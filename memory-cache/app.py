import logging
from flask import Flask
from config import Config
from routes import api_bp
from database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Register API blueprint
    app.register_blueprint(api_bp)

    # Initialize DB table if in dev mode and DATABASE_URL is supplied
    if Config.DATABASE_URL:
        try:
            init_db()
        except Exception as e:
            logger.warning(f"Database initialization deferred or failed: {e}")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=(Config.APP_ENV == "development"))