"""Standalone entry point for the web analytics dashboard."""

import os

from running_coach_ai.config import settings
from running_coach_ai.web.app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.WEB_PORT))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
