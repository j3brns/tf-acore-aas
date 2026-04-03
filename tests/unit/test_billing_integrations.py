from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.billing import integrations


class FakeBoto3:
    def __init__(self) -> None:
        self.clients: dict[tuple[str, str], object] = {}

    def client(self, service_name: str, *, region_name: str) -> object:
        key = (service_name, region_name)
        return self.clients.setdefault(key, object())


def test_build_billing_integrations_creates_clients_and_pricing_provider(monkeypatch) -> None:
    fake_boto3 = FakeBoto3()
    pricing_provider = object()

    monkeypatch.setattr(integrations, "boto3", fake_boto3)
    monkeypatch.setattr(integrations, "SSMProvider", lambda *, boto3_client: pricing_provider)

    deps = integrations.build_billing_integrations(region="eu-west-2")

    assert deps.ssm is fake_boto3.client("ssm", region_name="eu-west-2")
    assert deps.events is fake_boto3.client("events", region_name="eu-west-2")
    assert deps.cloudwatch is fake_boto3.client("cloudwatch", region_name="eu-west-2")
    assert deps.pricing_provider is pricing_provider
