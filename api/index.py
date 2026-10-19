import os
import sys
from pathlib import Path

# Add the project root directory to Python's module search path
# This ensures imports like 'import db', 'from routers import ...', etc. work seamlessly on Vercel Serverless Functions
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import app

# Vercel ASGI entrypoint
__all__ = ["app"]
