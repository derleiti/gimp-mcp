from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GimpMcpError(RuntimeError):
    code: str
    message: str
    recoverable: bool = True

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "recoverable": self.recoverable}}
