import json
import re
from datetime import date, datetime
from uuid import UUID

from shared.config import settings

SENSITIVE = re.compile(
    r"api.?key|authorization|password|secret|access.?token|phone|contact|address", re.I
)


def clean(value):
    """Only serialize data, never model clients/runtime contexts or credentials."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if SENSITIVE.search(str(k)) and not str(k).endswith("_id")
            else clean(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, str):
        smith_key = settings.langsmith_api_key.get_secret_value()
        if smith_key:
            value = value.replace(smith_key, "[SECRET]")
        if settings.openai_api_key:
            value = value.replace(settings.openai_api_key, "[SECRET]")
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return json.dumps(clean(parsed), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
        value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[PHONE]", value)
        value = re.sub(r"(?i)(bearer\s+|sk-)[\w.\-]+", "[SECRET]", value)
        value = re.sub(
            r'[\u4e00-\u9fff]{2,}(?:省|市)[^\s，。；\n"}]{2,}(?:室|号|楼)', "[ADDRESS]", value
        )
        return value
    if value is None or isinstance(value, (int, float, bool, UUID, date, datetime)):
        return value
    return {"type": type(value).__name__}
