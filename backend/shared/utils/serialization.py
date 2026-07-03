import json
from typing import Any

from pydantic import BaseModel


def to_json(payload: BaseModel | dict[str, Any]) -> str:
    if isinstance(payload, BaseModel):
        return payload.model_dump_json()

    return json.dumps(payload, default=str)
