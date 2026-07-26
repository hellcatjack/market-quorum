import json
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "platform/src"))
os.environ.setdefault(
    "TRADINGNG_DATABASE_URL",
    "postgresql+psycopg://tradingng:tradingng@127.0.0.1:5432/tradingng",
)

from tradingng_platform.api.app import create_app


def main() -> None:
    schema = create_app().openapi()
    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    duplicates = sorted(
        operation_id
        for operation_id, count in Counter(operation_ids).items()
        if count > 1
    )
    if duplicates:
        raise SystemExit(f"duplicate OpenAPI operation IDs: {', '.join(duplicates)}")

    destination = PROJECT_ROOT / "var/openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"exported {len(schema['paths'])} paths to {destination}")


if __name__ == "__main__":
    main()
