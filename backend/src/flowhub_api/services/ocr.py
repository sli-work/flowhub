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
        self._failed = False
        self._lock = asyncio.Lock()

    async def _engine_or_none(self):
        if self._engine is not None:
            return self._engine
        if self._failed:
            return None
        async with self._lock:
            if self._engine is not None:
                return self._engine
            if self._failed:
                return None
            try:
                import rapidocr_onnxruntime
            except ImportError:
                logger.warning("rapidocr-onnxruntime 未安装，OCR 不可用")
                self._failed = True
                return None
            try:
                self._engine = await asyncio.to_thread(rapidocr_onnxruntime.RapidOCR)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RapidOCR 引擎初始化失败，OCR 不可用：%s", exc)
                self._failed = True
                return None
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

    # 直接读 _values 内存快照而非 settings()：settings() 对 bool 字段做
    # type(annotation)(value)，bool("false") == True（runtime_config.py:23-25 bug），
    # 会把存了 "false" 的管理员开关误判为启用；此处按字符串 "true" 判定。
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
