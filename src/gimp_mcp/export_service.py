from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .errors import GimpMcpError
from .operations import GimpOperations
from .sessions import ArtworkSession


class ExportService:
    """Export the canonical session document to project or raster formats."""

    def __init__(self, ops: GimpOperations) -> None:
        self.ops = ops

    @staticmethod
    def normalize_target(path: str | Path, fmt: str) -> Path:
        fmt = fmt.lower().strip().lstrip('.')
        if fmt == 'jpeg':
            fmt = 'jpg'
        if fmt not in {'xcf', 'png', 'jpg'}:
            raise GimpMcpError('INVALID_ARGUMENT', f'Unsupported export format: {fmt}', False)
        target = Path(path).expanduser()
        suffix = '.jpg' if fmt == 'jpg' else f'.{fmt}'
        if target.suffix.lower() not in ({'.jpg', '.jpeg'} if fmt == 'jpg' else {suffix}):
            target = target.with_suffix(suffix)
        return target

    def export(self, session: ArtworkSession, target: str | Path, fmt: str, *, overwrite: bool = False) -> dict[str, Any]:
        fmt = fmt.lower().strip().lstrip('.')
        if fmt == 'jpeg':
            fmt = 'jpg'
        dst = self.normalize_target(target, fmt)
        if not session.document.exists():
            raise GimpMcpError('INVALID_PATH', 'Session document does not exist', False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not overwrite:
            raise GimpMcpError('INVALID_PATH', f'Output exists: {dst}', True)
        if fmt == 'xcf':
            shutil.copy2(session.document, dst)
            return {'output': str(dst), 'format': 'xcf', 'source': str(session.document)}
        data = self.ops.export(session, dst)
        data['format'] = fmt
        return data
