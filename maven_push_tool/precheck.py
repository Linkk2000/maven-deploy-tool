from __future__ import annotations

import base64
import socket
import urllib.error
import urllib.request

from .config import AppConfig
from .models import (
    ArtifactRecord,
    PRECHECK_EXISTS,
    PRECHECK_FAILED,
    PRECHECK_NOT_FOUND,
)
from .models import SettingsInfo


def precheck_remote(
    record: ArtifactRecord,
    config: AppConfig,
    settings_info: SettingsInfo,
) -> str:
    urls = [record.remote_pom_url]
    if record.packaging != "pom" and record.remote_main_url:
        urls.append(record.remote_main_url)

    saw_not_found = False
    for url in urls:
        status, message = check_url(url, record.target_repo_id, config, settings_info)
        if status == PRECHECK_EXISTS:
            record.precheck_status = PRECHECK_EXISTS
            return PRECHECK_EXISTS
        if status == PRECHECK_NOT_FOUND:
            saw_not_found = True
            continue
        record.precheck_status = PRECHECK_FAILED
        record.error_stage = "precheck"
        record.error_message = message
        return PRECHECK_FAILED

    record.precheck_status = PRECHECK_NOT_FOUND if saw_not_found else PRECHECK_FAILED
    if record.precheck_status == PRECHECK_FAILED:
        record.error_stage = "precheck"
        record.error_message = "预检未返回明确结果。"
        return PRECHECK_FAILED
    return PRECHECK_NOT_FOUND


def check_url(
    url: str | None,
    server_id: str | None,
    config: AppConfig,
    settings_info: SettingsInfo,
) -> tuple[str, str]:
    if not url:
        return PRECHECK_FAILED, "预检 URL 为空。"

    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method)
        attach_auth_header(request, server_id, config, settings_info)
        try:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                if 200 <= response.status < 300:
                    return PRECHECK_EXISTS, f"{method} {response.status}"
                if response.status == 404:
                    return PRECHECK_NOT_FOUND, f"{method} 404"
                return PRECHECK_FAILED, f"{method} {response.status}"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return PRECHECK_NOT_FOUND, f"{method} 404"
            if exc.code in {405, 501} and method == "HEAD":
                continue
            return PRECHECK_FAILED, f"{method} {exc.code} {exc.reason}"
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            return PRECHECK_FAILED, f"{method} 请求失败: {exc}"
    return PRECHECK_FAILED, "预检请求失败。"


def attach_auth_header(
    request: urllib.request.Request,
    server_id: str | None,
    config: AppConfig,
    settings_info: SettingsInfo,
) -> None:
    username = None
    password = None

    if config.username and config.password:
        username = config.username
        password = config.password
    elif config.auth_from_settings and server_id and server_id in settings_info.servers:
        server = settings_info.servers[server_id]
        username = server.username
        password = server.password

    if not username or password is None:
        return

    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {token}")
