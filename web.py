"""Standalone entry point for the web analytics dashboard."""

from running_coach_ai.config import settings
from running_coach_ai.web.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.WEB_PORT, debug=True, use_reloader=False)
