import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Ensure ENV_FILE points to backend/.env.dev if present
env_dev_path = backend_dir / ".env.dev"
if env_dev_path.exists():
    os.environ.setdefault("ENV_FILE", str(env_dev_path))
