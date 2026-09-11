from pathlib import Path
from unittest.mock import Mock, patch

from gimp_mcp.headless import HeadlessGimpManager, HeadlessGimpPool


def test_headless_manager_reuses_ready_runtime(tmp_path: Path):
    manager = HeadlessGimpManager(state_dir=tmp_path)
    manager._ping = Mock(return_value={'ok': True, 'mode': 'headless'})
    with patch('gimp_mcp.headless.subprocess.Popen') as popen:
        status = manager.ensure()
    assert status['ok'] is True
    assert status['started'] is False
    popen.assert_not_called()


def test_headless_paths_are_private_state(tmp_path: Path):
    manager = HeadlessGimpManager(state_dir=tmp_path)
    assert manager.socket_path.parent == tmp_path
    assert manager.socket_path.name == 'gimp-headless.sock'


def test_pool_routes_same_session_stably(tmp_path: Path):
    pool=HeadlessGimpPool(workers=3,state_dir=tmp_path)
    assert pool.for_key('/tmp/a.xcf') is pool.for_key('/tmp/a.xcf')
    assert len(pool.workers) == 3
