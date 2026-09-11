from pathlib import Path
from unittest.mock import Mock

from gimp_mcp.service_manager import ServiceManager


def test_unit_is_user_service_and_restarts(tmp_path: Path, monkeypatch):
    root=tmp_path/'project'; (root/'.venv/bin').mkdir(parents=True); (root/'.venv/bin/python').write_text('')
    manager=ServiceManager(root,tmp_path/'state')
    monkeypatch.setattr(Path,'home',classmethod(lambda cls: tmp_path/'home'))
    manager.unit_dir=tmp_path/'home/.config/systemd/user'; manager.unit_path=manager.unit_dir/manager.UNIT
    manager._run=Mock(return_value=type('R',(),{'returncode':0,'stdout':'','stderr':''})())
    monkeypatch.setattr(manager,'status',lambda:{'installed':True})
    monkeypatch.setattr(manager,'stop_legacy_server',Mock(return_value=False))
    assert manager.install()['installed'] is True
    text=manager.unit_path.read_text()
    assert 'Restart=on-failure' in text
    assert 'WantedBy=default.target' in text
    assert 'GIMP_MCP_STATE_DIR=' in text
    calls=[x.args for x in manager._run.call_args_list]
    assert ('enable', manager.UNIT) in calls
    assert ('restart', manager.UNIT) in calls


def test_status_not_installed(tmp_path: Path):
    manager=ServiceManager(tmp_path,tmp_path/'state')
    manager.unit_path=tmp_path/'missing.service'
    assert manager.status()['active']=='not-installed'


def test_stop_legacy_server_rejects_unrelated_pid(tmp_path: Path, monkeypatch):
    manager=ServiceManager(tmp_path,tmp_path/'state'); manager.state_dir.mkdir(); (manager.state_dir/'server.pid').write_text('123')
    original=Path.read_bytes
    def fake_read(self):
        if str(self)=='/proc/123/cmdline': return b'python\x00something_else'
        return original(self)
    monkeypatch.setattr(Path,'read_bytes',fake_read)
    assert manager.stop_legacy_server() is False
