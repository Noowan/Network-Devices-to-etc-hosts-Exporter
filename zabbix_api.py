#!/usr/bin/env python3

import json
import re
import requests


TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
MODEL_RE = re.compile(r"^[a-z0-9_.-]+$")


class ExportError(Exception):
    pass


class ZabbixAPI:
    def __init__(
        self,
        url,
        token,
        auth_mode="bearer",
        verify=True,
        timeout=30,
    ):
        if url.endswith("/api_jsonrpc.php"):
            self.url = url
        else:
            self.url = url.rstrip("/") + "/api_jsonrpc.php"

        self.token = token
        self.auth_mode = auth_mode
        self.verify = verify
        self.timeout = timeout
        self.request_id = 0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json-rpc",
                "Accept": "application/json",
                "User-Agent": "zabbix-to-oxidized/1.0",
            }
        )

        if self.auth_mode == "bearer":
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def call(self, method, params):
        self.request_id += 1

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self.request_id,
        }

        # Для старых версий Zabbix можно передавать токен
        # в поле auth JSON-RPC-запроса.
        if self.auth_mode == "jsonrpc":
            payload["auth"] = self.token

        try:
            response = self.session.post(
                self.url,
                data=json.dumps(payload),
                timeout=self.timeout,
                verify=self.verify,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ExportError(f"Ошибка обращения к Zabbix API: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            text = response.text[:500]
            raise ExportError(
                f"Zabbix вернул не JSON. Начало ответа: {text!r}"
            ) from exc

        if "error" in data:
            error = data["error"]
            raise ExportError(
                "Ошибка Zabbix API: "
                f"code={error.get('code')}, "
                f"message={error.get('message')}, "
                f"data={error.get('data')}"
            )

        if "result" not in data:
            raise ExportError("В ответе Zabbix API отсутствует поле result")

        return data["result"]


