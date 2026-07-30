"""Export the deterministic ADT OpenAPI contract without starting services."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# Contract generation must never inherit developer or production credentials.
_CONTRACT_ENVIRONMENT = {
    "SUPABASE_URL": "https://openapi.example.invalid",
    "SUPABASE_PUBLISHABLE_KEY": "openapi-public-placeholder",
    "SUPABASE_DATABASE_URL": "postgresql://openapi@db.example.invalid/adt",
    "ADT_ENVIRONMENT": "test",
    "ADT_LOG_LEVEL": "WARNING",
    "ADT_CORS_ORIGINS": '["http://localhost:5173"]',
}
os.environ.update(_CONTRACT_ENVIRONMENT)

from app.main import app  # noqa: E402  # Environment must be isolated first.


def main() -> None:
    """Write OpenAPI JSON to the single requested output path."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_openapi.py OUTPUT_PATH")

    output_path = Path(sys.argv[1]).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
