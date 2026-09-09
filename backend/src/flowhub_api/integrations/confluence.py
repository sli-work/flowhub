from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlencode

import httpx


class ConfluenceError(RuntimeError):
    """Confluence API request failed."""


class ConfluenceClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._verify_ssl = verify_ssl
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=self._auth,
            verify=verify_ssl,
            timeout=timeout_seconds,
            # Company Confluence instances are commonly published through a
            # gateway which redirects an unqualified URL to its canonical path.
            # Treat that redirect as part of the connection, not as an API body.
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | list[Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        try:
            response = await self._client.request(
                method.upper(),
                path,
                params=params,
                json=json,
                data=data,
                files=files,
                headers=headers,
            )
        except httpx.ConnectError as exc:
            try:
                return await self._curl_request(
                    method, path, params, json, data, files, headers
                )
            except ConfluenceError as fallback_exc:
                # Do not hide the HTTP client's TLS/network cause when the
                # optional compatibility transport is unavailable or fails.
                raise ConfluenceError(
                    f"{method.upper()} {path} connection failed: {exc}; "
                    f"curl fallback failed: {fallback_exc}"
                ) from exc
        except httpx.TimeoutException as exc:
            raise ConfluenceError(
                f"{method.upper()} {path} timed out after {self._timeout:g}s; "
                "check the FlowHub server network route and timeout setting"
            ) from exc
        except httpx.HTTPError as exc:
            raise ConfluenceError(
                f"{method.upper()} {path} transport error: {exc}"
            ) from exc

        if response.status_code >= 400:
            body = response.text.strip().replace("\n", " ")[:500]
            raise ConfluenceError(
                f"{method.upper()} {path} failed ({response.status_code}): {body}"
            )

        if not response.content:
            return {"ok": True, "status_code": response.status_code}

        ctype = response.headers.get("content-type", "")
        if "application/json" in ctype:
            return response.json()
        # REST endpoints should return JSON. HTML here almost always means a
        # login page, a reverse-proxy error page, or a wrong base URL.
        preview = response.text.strip().replace("\n", " ")[:160]
        raise ConfluenceError(
            f"{method.upper()} {path} returned non-JSON content ({ctype or 'unknown'}): "
            f"{preview}"
        )

    async def _curl_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | list[Any] | None,
        data: dict[str, Any] | None,
        files: dict[str, tuple[str, bytes, str]] | None,
        headers: dict[str, str] | None,
    ) -> Any:
        if shutil.which("curl") is None:
            raise ConfluenceError("curl not available for fallback transport")

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        cmd = [
            "curl",
            "-sS",
            "-L",
            "--max-time",
            str(int(self._timeout)),
        ]
        if not self._verify_ssl:
            cmd.append("-k")
        cmd += [
            "-X",
            method.upper(),
            "-u",
            f"{self._auth[0]}:{self._auth[1]}",
            "-H",
            "Accept: application/json",
        ]
        if headers:
            for key, value in headers.items():
                cmd += ["-H", f"{key}: {value}"]

        stdin_data: str | None = None
        temp_paths: list[str] = []
        try:
            if files:
                for name, (filename, content, ctype) in files.items():
                    fd, tmp = tempfile.mkstemp()
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(content)
                    temp_paths.append(tmp)
                    cmd += [
                        "-F",
                        f"{name}=@{tmp};filename={filename};type={ctype}",
                    ]
            if json_body is not None:
                stdin_data = json.dumps(json_body)
                cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
            elif data:
                for key, value in data.items():
                    cmd += ["--data-urlencode", f"{key}={value}"]
            cmd.append(url)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, err = await proc.communicate(
                input=stdin_data.encode() if stdin_data is not None else None
            )
        finally:
            for p in temp_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass

        if proc.returncode != 0:
            raise ConfluenceError(
                f"{method.upper()} {path} via curl failed "
                f"(exit {proc.returncode}): {err.decode(errors='replace')[:500]}"
            )

        if not out:
            return {"ok": True, "status_code": 0}

        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"raw_text": out.decode(errors="replace"), "status_code": 0}
