from __future__ import annotations

import os
from typing import Any

import boto3

_session_cache: dict[str, Any] = {}
_client_cache: dict[tuple[str, str], Any] = {}
_resource_cache: dict[tuple[str, str], Any] = {}


def aws_region() -> str:
    return os.environ["AWS_REGION"]


def boto3_session(*, region_name: str | None = None) -> Any:
    region = region_name or aws_region()
    session = _session_cache.get(region)
    if session is None:
        session = boto3.session.Session(region_name=region)
        _session_cache[region] = session
    return session


def boto3_client(service_name: str, *, region_name: str | None = None) -> Any:
    region = region_name or aws_region()
    key = (service_name, region)
    client = _client_cache.get(key)
    if client is None:
        client = boto3_session(region_name=region).client(service_name, region_name=region)
        _client_cache[key] = client
    return client


def boto3_resource(service_name: str, *, region_name: str | None = None) -> Any:
    region = region_name or aws_region()
    key = (service_name, region)
    resource = _resource_cache.get(key)
    if resource is None:
        resource = boto3_session(region_name=region).resource(service_name, region_name=region)
        _resource_cache[key] = resource
    return resource


def reset_caches() -> None:
    _session_cache.clear()
    _client_cache.clear()
    _resource_cache.clear()
