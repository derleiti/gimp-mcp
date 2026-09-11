from pathlib import Path

import pytest

from gimp_mcp.export_service import ExportService
from gimp_mcp.sessions import ArtworkSession


class DummyOps:
    def __init__(self): self.calls=[]
    def export(self, session, output):
        self.calls.append((session, output)); output.write_bytes(b'img'); return {'output':str(output),'width':1,'height':1}


def test_xcf_export_copies_canonical_document(tmp_path: Path):
    doc=tmp_path/'document.xcf'; doc.write_bytes(b'xcf')
    session=ArtworkSession('s',tmp_path,doc)
    service=ExportService(DummyOps())
    result=service.export(session,tmp_path/'copy','xcf')
    out=Path(result['output'])
    assert out.name=='copy.xcf' and out.read_bytes()==b'xcf'


def test_raster_export_uses_gimp_operations_and_normalizes_suffix(tmp_path: Path):
    doc=tmp_path/'document.xcf'; doc.write_bytes(b'xcf')
    session=ArtworkSession('s',tmp_path,doc)
    ops=DummyOps(); service=ExportService(ops)
    result=service.export(session,tmp_path/'image','jpg')
    assert result['format']=='jpg'
    assert Path(result['output']).suffix=='.jpg'
    assert len(ops.calls)==1

def test_public_export_creates_three_opaque_links(tmp_path: Path):
    from gimp_mcp.public_exports import PublicExportManager
    doc=tmp_path/'document.xcf'; doc.write_bytes(b'xcf'); session=ArtworkSession('s',tmp_path,doc)
    manager=PublicExportManager(tmp_path/'public',ExportService(DummyOps()),base_url='https://ailinux.me/gimp-mcp/download')
    result=manager.publish(session,ttl=300)
    assert set(result['links']) == {'xcf','jpg','png'}
    for fmt,item in result['links'].items():
        assert item['url'].startswith('https://ailinux.me/gimp-mcp/download/') and item['url'].endswith('/artwork.'+fmt)
