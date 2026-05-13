"""Constants package — project-wide enumerations and configuration."""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels above this file: src/constants -> src -> root).
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
