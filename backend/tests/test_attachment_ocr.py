"""OCR 适配器：Noop 降级 / RapidOCR 可用性 / get_ocr_adapter 开关。"""
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
