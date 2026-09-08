"""附件 OCR 适配器：本地 RapidOCR（CPU，中英文）或 Noop 降级。

开关：RuntimeConfig 内存快照 `attachment_ocr_enabled == "true"` 且依赖可 import 时启用，
否则回退 Noop（扫描件标记「需 OCR」，不伪造文本）。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class OcrAdapter:
    name: str = "unknown"
    available: bool = False

    async def extract_text(self, image_bytes: bytes, page_no: int) -> str:
        raise NotImplementedError


class NoopOcrAdapter(OcrAdapter):
    name = "noop"
    available = False

    async def extract_text(self, image_bytes: bytes, page_no: int) -> str:
        return ""


class RapidOcrAdapter(OcrAdapter):
    name = "rapidocr"
    available = True

    def __init__(self) -> None:
        self._engine = None
        self._lock = asyncio.Lock()

    async def _engine_or_none(self):
        if self._engine is not None:
            return self._engine
        async with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                import rapidocr_onnxruntime
            except ImportError:
                logger.warning("rapidocr-onnxruntime 未安装，OCR 不可用")
                return None
            self._engine = rapidocr_onnxruntime.RapidOCR()
            return self._engine

    async def extract_text(self, image_bytes: bytes, page_no: int) -> str:
        engine = await self._engine_or_none()
        if engine is None:
            return ""
        try:
            result, _ = await asyncio.to_thread(engine, image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR 第 %s 页失败：%s", page_no, exc)
            return ""
        if not result:
            return ""
        return "\n".join(item[1] for item in result if len(item) >= 2 and item[1])


def get_ocr_adapter() -> OcrAdapter:
    """按运行时配置 + 依赖可用性返回 OCR 适配器；不可用时永远返回 Noop，不抛错。"""
    from flowhub_api.services import runtime_config

    try:
        raw = runtime_config._values.get("attachment_ocr_enabled", "")
        enabled = str(raw).strip().lower() == "true"
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled:
        return NoopOcrAdapter()
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return NoopOcrAdapter()
    return RapidOcrAdapter()
