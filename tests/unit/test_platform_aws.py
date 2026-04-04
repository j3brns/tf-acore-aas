from __future__ import annotations

from src import platform_aws


class FakeSession:
    def __init__(self, region_name: str) -> None:
        self.region_name = region_name
        self.clients: dict[tuple[str, str], object] = {}
        self.resources: dict[tuple[str, str], object] = {}

    def client(self, service_name: str, *, region_name: str) -> object:
        key = (service_name, region_name)
        value = object()
        self.clients.setdefault(key, value)
        return self.clients[key]

    def resource(self, service_name: str, *, region_name: str) -> object:
        key = (service_name, region_name)
        value = object()
        self.resources.setdefault(key, value)
        return self.resources[key]


class FakeBoto3:
    class session:
        @staticmethod
        def Session(*, region_name: str) -> FakeSession:
            return FakeSession(region_name)


def test_boto3_helpers_cache_sessions_clients_and_resources(monkeypatch) -> None:
    monkeypatch.setattr(platform_aws, "boto3", FakeBoto3())
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    platform_aws.reset_caches()

    try:
        session = platform_aws.boto3_session()
        assert session is platform_aws.boto3_session()
        assert platform_aws.boto3_client("ssm") is platform_aws.boto3_client("ssm")
        assert platform_aws.boto3_resource("dynamodb") is platform_aws.boto3_resource("dynamodb")
    finally:
        platform_aws.reset_caches()


def test_aws_region_requires_environment(monkeypatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    platform_aws.reset_caches()

    try:
        try:
            platform_aws.aws_region()
        except KeyError as exc:
            assert str(exc) == "'AWS_REGION'"
        else:
            raise AssertionError("aws_region() should require AWS_REGION")
    finally:
        platform_aws.reset_caches()
