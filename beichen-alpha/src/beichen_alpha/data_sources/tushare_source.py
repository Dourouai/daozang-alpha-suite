from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TUSHARE_API_URL = "http://api.tushare.pro"


class TushareNotConfigured(RuntimeError):
    pass


class TushareApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class TushareConfig:
    token: str = ""
    api_url: str = TUSHARE_API_URL
    timeout: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.token.strip())


def load_tushare_config(env_path: str | Path = "config/local.env") -> TushareConfig:
    env_values = read_env_file(env_path)
    token = os.environ.get("TUSHARE_TOKEN") or env_values.get("TUSHARE_TOKEN", "")
    api_url = os.environ.get("TUSHARE_API_URL") or env_values.get("TUSHARE_API_URL", TUSHARE_API_URL)
    timeout = to_float(os.environ.get("TUSHARE_TIMEOUT") or env_values.get("TUSHARE_TIMEOUT"), 10.0)
    return TushareConfig(token=token.strip(), api_url=api_url.strip() or TUSHARE_API_URL, timeout=timeout)


def call_tushare_api(
    api_name: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    fields: str = "",
    api_url: str = TUSHARE_API_URL,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    if not token.strip():
        raise TushareNotConfigured("TUSHARE_TOKEN is not configured")

    payload = json.dumps(
        {
            "api_name": api_name,
            "token": token,
            "params": params or {},
            "fields": fields,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")

    parsed = json.loads(body)
    if parsed.get("code") != 0:
        raise TushareApiError(str(parsed.get("msg") or parsed))

    data = parsed.get("data") or {}
    columns = data.get("fields") or []
    items = data.get("items") or []
    return [dict(zip(columns, item)) for item in items]


def read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists() or env_path.is_dir():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export "):]
        if "=" not in text:
            continue
        key, _, value = text.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
