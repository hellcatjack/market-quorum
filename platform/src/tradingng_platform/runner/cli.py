import argparse
import sys
from pathlib import Path

from tradingng_platform.config import Settings
from tradingng_platform.runner.contracts import RunnerInput
from tradingng_platform.runner.events import EventEmitter
from tradingng_platform.runner.tradingagents import TradingAgentsRunner


def _stdout_sink(event) -> None:
    print(event.model_dump_json(), flush=True)


def classify_runner_error(error: Exception) -> str:
    error_type = type(error).__name__.lower()
    if ("vendor" in error_type or "alpha" in error_type) and "ratelimit" in error_type:
        return "vendor_rate_limit"
    if any(marker in error_type for marker in ("ratelimit", "toomanyrequests", "overload")):
        return "gateway_overload"
    if any(
        marker in error_type
        for marker in ("timeout", "connection", "internalserver", "serviceunavailable")
    ):
        return "gateway_unavailable"
    return "runner_unhandled_error"


def run(config_path: Path) -> int:
    emitter = EventEmitter(_stdout_sink)
    try:
        settings = Settings()
        job_dir = settings.job_dir.resolve()
        if not config_path.is_absolute():
            raise ValueError("runner config path must be absolute")
        resolved_config = config_path.resolve(strict=True)
        if not resolved_config.is_relative_to(job_dir) or not resolved_config.is_file():
            raise ValueError("runner config must be a file beneath the job directory")
        runner_input = RunnerInput.model_validate_json(resolved_config.read_text(encoding="utf-8"))
        work_dir = runner_input.work_dir.resolve()
        if not work_dir.is_relative_to(job_dir):
            raise ValueError("runner work directory must be beneath the job directory")
        TradingAgentsRunner(runner_input, emitter=emitter).run()
        return 0
    except Exception as exc:
        emitter.emit(
            "error",
            "runner.failed",
            {
                "error_type": type(exc).__name__,
                "error_code": classify_runner_error(exc),
            },
        )
        print(f"runner_error={type(exc).__name__}", file=sys.stderr, flush=True)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="tradingng-platform-runner")
    parser.add_argument("--config", required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.config))


if __name__ == "__main__":
    main()
