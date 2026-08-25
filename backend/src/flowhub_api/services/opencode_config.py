"""opencode 客户端工具配置（auth.json + opencode.json 读写 + 模型列表同步）。

opencode `auth login` 是 TUI 交互式登录，**无法非交互写 key**——必须直接读写：
- `~/.local/share/opencode/auth.json`：`{"<provider>": ["<type>", "<key>"]}`（type=api/oauth）
- `~/.opencode/opencode.json`：provider 块（baseURL / models）：
  ```json
  {"provider": {"<name>": {"options": {"baseURL": "..."}, "models": {"<model>": {}}}}}
  ```
  合并写入（保留 $schema / plugin 等已有配置），原子替换。
"""
import json
import os
import subprocess
from pathlib import Path

AUTH_PATH = Path(os.environ.get("OPENCODE_AUTH_PATH") or Path.home() / ".local" / "share" / "opencode" / "auth.json")
CONFIG_PATH = Path(os.environ.get("OPENCODE_CONFIG_PATH") or Path.home() / ".opencode" / "opencode.json")
MODELS_TIMEOUT = 30


def _read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text("utf-8") or "{}")
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _write_json(path: Path, data: dict) -> None:
    """原子写：临时文件 + rename（避免 opencode 读时半写状态）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def _read_auth() -> dict:
    return _read_json(AUTH_PATH, {})


def _write_auth(data: dict) -> None:
    _write_json(AUTH_PATH, data)


def _read_config() -> dict:
    return _read_json(CONFIG_PATH, {})


def _write_config(data: dict) -> None:
    _write_json(CONFIG_PATH, data)


def list_providers() -> list[dict]:
    """已配置的 providers 列表：[{provider, type, hasKey, baseUrl, models}]。

    baseUrl/models 从 opencode.json 的 provider 块读取（自定义 provider 配置）。
    """
    cfg = _read_config().get("provider") or {}
    out = []
    for p, val in _read_auth().items():
        if isinstance(val, list) and len(val) >= 2:
            kind, key = val[0], val[1]
        elif isinstance(val, dict):  # 旧/兼容格式 {type, key}
            kind, key = val.get("type", "api"), val.get("key", "")
        else:
            continue
        block = cfg.get(p) or {}
        models = list((block.get("models") or {}).keys()) if isinstance(block.get("models"), dict) else []
        out.append({
            "provider": p, "type": kind, "hasKey": bool(key),
            "baseUrl": (block.get("options") or {}).get("baseURL", "") if isinstance(block.get("options"), dict) else "",
            "models": models,
        })
    return out


def set_provider_config(
    provider: str, key: str, kind: str = "api",
    base_url: str = "", models: list[str] | None = None,
) -> dict:
    """完整配置一个 provider：写 auth.json（key）+ opencode.json（baseURL/models）。

    - base_url 为空：删除 options.baseURL（使用 opencode 内置默认端点）
    - models 为空：删除 models 块（使用 opencode 内置模型列表）
    - 均合并写入，保留已有配置（$schema / plugin 等）。
    """
    if not provider:
        raise ValueError("provider 不能为空")
    # 1) auth.json：key
    auth = _read_auth()
    if key:
        auth[provider] = [kind, key]
    elif provider not in auth:
        raise ValueError("新 Provider 必须提供 API Key")
    _write_auth(auth)
    # 2) opencode.json：provider 块（baseURL + models）
    cfg = _read_config()
    providers = cfg.setdefault("provider", {})
    block = providers.setdefault(provider, {})
    if base_url:
        block.setdefault("options", {})["baseURL"] = base_url
    elif isinstance(block.get("options"), dict):
        block["options"].pop("baseURL", None)
        if not block["options"]:
            block.pop("options", None)
    models = [m for m in (models or []) if m.strip()]
    if models:
        block["models"] = {m.strip(): {} for m in models}
    else:
        block.pop("models", None)
    _write_config(cfg)
    return {"provider": provider, "type": kind, "hasKey": bool(auth.get(provider)), "baseUrl": base_url, "models": models}


def get_provider_config(provider: str) -> dict:
    """读取某 provider 的完整配置（auth key 状态 + opencode.json baseURL/models）。"""
    auth = _read_auth()
    val = auth.get(provider)
    if isinstance(val, list) and len(val) >= 2:
        kind, key = val[0], val[1]
    elif isinstance(val, dict):
        kind, key = val.get("type", "api"), val.get("key", "")
    else:
        kind, key = "api", ""
    cfg = _read_config().get("provider") or {}
    block = cfg.get(provider) or {}
    models = list((block.get("models") or {}).keys()) if isinstance(block.get("models"), dict) else []
    return {
        "provider": provider, "type": kind, "hasKey": bool(key),
        "baseUrl": (block.get("options") or {}).get("baseURL", "") if isinstance(block.get("options"), dict) else "",
        "models": models,
    }


def list_models() -> list[dict]:
    """调用 `opencode models` 解析为 [{provider, model}] 列表（用于 seed 同步 + 列表展示）。"""
    try:
        out = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=MODELS_TIMEOUT)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    rows: list[dict] = []
    for line in (out.stdout or "").splitlines():
        m = line.strip()
        if not m or "/" not in m:
            continue
        provider, model = m.split("/", 1)
        rows.append({"provider": provider.strip(), "model": model.strip()})
    return rows
