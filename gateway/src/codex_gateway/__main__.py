import logging

import uvicorn

from codex_gateway.config import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    uvicorn.run(
        "codex_gateway.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
