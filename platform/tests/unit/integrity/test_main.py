import uuid

import pytest

from tradingng_platform.integrity.main import parse_args


def test_parse_args_accepts_bounded_limit_and_optional_run_id():
    run_id = uuid.UUID(int=7)

    arguments = parse_args(["--limit", "25", "--run-id", str(run_id)])

    assert arguments.limit == 25
    assert arguments.run_id == run_id


@pytest.mark.parametrize("limit", ["0", "501"])
def test_parse_args_rejects_out_of_range_limit(limit):
    with pytest.raises(SystemExit):
        parse_args(["--limit", limit])
