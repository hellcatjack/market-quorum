from tradingng_platform.worker.main import build_worker_instance_name


def test_worker_name_uses_stable_systemd_instance():
    assert build_worker_instance_name("host-a", 1234, "17") == "host-a:17"


def test_worker_name_falls_back_to_pid_outside_systemd_pool():
    assert build_worker_instance_name("host-a", 1234, None) == "host-a:1234"
