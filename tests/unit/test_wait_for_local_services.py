from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "wait_for_local_services",
        repo_root / "scripts" / "wait_for_local_services.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


wait_for_local_services = _load_module()


class _FakeResponse:
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


def test_wait_for_service_retries_until_success() -> None:
    seen: list[str] = []

    def _fetcher(url: str, _timeout: float) -> object:
        seen.append(url)
        if len(seen) < 3:
            raise OSError("not ready")
        return _FakeResponse()

    sleeps: list[float] = []
    wait_for_local_services.wait_for_service(
        wait_for_local_services.ServiceCheck("mock runtime", "http://localhost:8765/ping"),
        timeout_seconds=5,
        interval_seconds=0.01,
        fetcher=_fetcher,
        sleep=sleeps.append,
    )

    assert len(seen) == 3
    assert sleeps == [0.01, 0.01]


def test_wait_for_service_times_out_with_service_name() -> None:
    def _fetcher(_url: str, _timeout: float) -> object:
        raise OSError("connection refused")

    with pytest.raises(TimeoutError, match="mock JWKS"):
        wait_for_local_services.wait_for_service(
            wait_for_local_services.ServiceCheck("mock JWKS", "http://localhost:8766/health"),
            timeout_seconds=0,
            interval_seconds=0.01,
            fetcher=_fetcher,
            sleep=lambda _seconds: None,
        )


def test_main_returns_non_zero_when_a_service_never_becomes_ready(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        wait_for_local_services,
        "wait_for_all_services",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("boom")),
    )

    rc = wait_for_local_services.main(["--timeout-seconds", "1"])

    assert rc == 1
    assert "boom" in capsys.readouterr().err


def test_verify_seeded_state_rejects_missing_env_test_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeDdb:
        def list_tables(self) -> dict[str, list[str]]:
            return {"TableNames": list(wait_for_local_services.REQUIRED_TABLES)}

    class _FakeSsm:
        def get_parameters(self, *, Names: list[str]) -> dict[str, list[dict[str, str]]]:
            return {"Parameters": [{"Name": name, "Value": "ok"} for name in Names]}

    clients = iter([_FakeDdb(), _FakeSsm()])

    monkeypatch.setattr(
        wait_for_local_services.boto3,
        "client",
        lambda *args, **kwargs: next(clients),
    )

    with pytest.raises(RuntimeError, match="missing env file"):
        wait_for_local_services.verify_seeded_state(
            localstack_endpoint="http://localhost:4566",
            aws_region="eu-west-2",
            env_test_path=tmp_path / ".env.test",
        )


def test_main_returns_non_zero_when_seeded_state_is_incomplete(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        wait_for_local_services,
        "wait_for_all_services",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        wait_for_local_services,
        "verify_seeded_state",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("missing env file")),
    )

    rc = wait_for_local_services.main(["--check-seeded-state"])

    assert rc == 1
    assert "missing env file" in capsys.readouterr().err
