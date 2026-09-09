"""OCR 适配器：Noop 降级 / RapidOCR 可用性 / get_ocr_adapter 开关。"""
import builtins

import pytest

from flowhub_api.services import runtime_config
from flowhub_api.services.ocr import NoopOcrAdapter, RapidOcrAdapter, get_ocr_adapter


@pytest.mark.asyncio
async def test_noop_extract_text_returns_empty():
    adapter = NoopOcrAdapter()
    assert adapter.available is False
    assert await adapter.extract_text(b"fake-image", 1) == ""


@pytest.mark.asyncio
async def test_get_ocr_adapter_disabled_returns_noop(monkeypatch):
    monkeypatch.setattr(runtime_config, "_values", {"attachment_ocr_enabled": "false"})
    adapter = get_ocr_adapter()
    assert isinstance(adapter, NoopOcrAdapter)


@pytest.mark.asyncio
async def test_get_ocr_adapter_enabled_returns_rapidocr(monkeypatch):
    monkeypatch.setattr(runtime_config, "_values", {"attachment_ocr_enabled": "true"})
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        pytest.skip("rapidocr-onnxruntime 未安装")
    adapter = get_ocr_adapter()
    assert isinstance(adapter, RapidOcrAdapter)
    assert adapter.available is True


@pytest.mark.asyncio
async def test_get_ocr_adapter_enabled_import_error_returns_noop(monkeypatch):
    monkeypatch.setattr(runtime_config, "_values", {"attachment_ocr_enabled": "true"})
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rapidocr_onnxruntime":
            raise ImportError("rapidocr 未安装")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    adapter = get_ocr_adapter()
    assert isinstance(adapter, NoopOcrAdapter)


@pytest.mark.asyncio
async def test_engine_construction_failure_degrades_to_empty(monkeypatch):
    try:
        import rapidocr_onnxruntime
    except ImportError:
        pytest.skip("rapidocr-onnxruntime 未安装")

    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("model download failed")

    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", boom)
    adapter = RapidOcrAdapter()
    assert await adapter.extract_text(b"fake-image", 1) == ""
    assert await adapter.extract_text(b"fake-image", 2) == ""
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_extract_text_joins_result_lines(monkeypatch):
    try:
        import rapidocr_onnxruntime
    except ImportError:
        pytest.skip("rapidocr-onnxruntime 未安装")

    class FakeEngine:
        def __call__(self, image_bytes):
            return [[[0, 0, 1, 1], "文本", 0.9], [[0, 2, 1, 3], "第二行", 0.8]], None

    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", lambda: FakeEngine())
    adapter = RapidOcrAdapter()
    assert await adapter.extract_text(b"fake-image", 1) == "文本\n第二行"
