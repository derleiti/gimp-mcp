from pathlib import Path
import pytest
from gimp_mcp.security import PathPolicy
from gimp_mcp.errors import GimpMcpError

def test_path_policy(tmp_path: Path):
    root=tmp_path/'ok'; p=PathPolicy([root])
    assert p.resolve(str(root/'a.png')) == (root/'a.png').resolve()
    with pytest.raises(GimpMcpError): p.resolve(str(tmp_path/'nope.txt'))
