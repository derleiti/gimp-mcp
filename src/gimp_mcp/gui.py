from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from gimp_mcp.control_settings import ControlSettings
from gimp_mcp.live_bridge import LiveBridge
from gimp_mcp.setup_manager import SetupManager

from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget
)

STATE = Path(os.getenv("GIMP_MCP_STATE_DIR", str(Path.home() / ".local/state/gimp-mcp")))
EVENTS = STATE / "events.jsonl"
SESSIONS = Path(os.getenv("GIMP_MCP_SESSION_ROOT", "/tmp/gimp-mcp/sessions"))
PIDFILE = STATE / "server.pid"
CONTROL = ControlSettings(STATE / "control.json")


class ControlCenter(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GIMP MCP Control Center")
        self.resize(1180, 760)
        STATE.mkdir(parents=True, exist_ok=True)
        tabs = QTabWidget(); self.setCentralWidget(tabs)
        tabs.addTab(self._studio_tab(), "Studio")
        tabs.addTab(self._live_tab(), "Live")
        tabs.addTab(self._server_tab(), "Server")
        tabs.addTab(self._ai_tab(), "AI Control")
        tabs.addTab(self._setup_tab(), "Setup / Updates")
        tabs.addTab(self._settings_tab(), "Settings")
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(750)
        self.refresh()


    def _studio_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        top=QHBoxLayout()
        self.studio_provider=QComboBox(); self.studio_provider.addItems(["triforce","chatgpt","claude","gemini","mistral"])
        self.studio_model=QLineEdit(); self.studio_model.setPlaceholderText("model id")
        self.studio_mode=QComboBox(); self.studio_mode.addItems(["auto","live","batch"])
        top.addWidget(QLabel("Provider")); top.addWidget(self.studio_provider); top.addWidget(QLabel("Model")); top.addWidget(self.studio_model,1); top.addWidget(QLabel("Mode")); top.addWidget(self.studio_mode)
        v.addLayout(top)
        self.studio_prompt=QTextEdit(); self.studio_prompt.setPlaceholderText("Describe the artwork or edit you want GIMP to perform...")
        v.addWidget(QLabel("Prompt")); v.addWidget(self.studio_prompt,1)
        row=QHBoxLayout()
        run=QPushButton("RUN"); pause=QPushButton("PAUSE"); stop=QPushButton("STOP"); undo=QPushButton("UNDO"); redo=QPushButton("REDO"); cooler=QPushButton("MAKE IT COOLER")
        run.clicked.connect(self.studio_run); pause.clicked.connect(self.studio_pause); stop.clicked.connect(self.studio_stop); undo.clicked.connect(self.studio_undo); redo.clicked.connect(self.studio_redo); cooler.clicked.connect(self.studio_cooler)
        for b in (run,pause,stop,undo,redo,cooler): row.addWidget(b)
        row.addStretch(); v.addLayout(row)
        self.studio_status=QLabel("No active job")
        self.studio_plan=QTextEdit(); self.studio_plan.setReadOnly(True)
        v.addWidget(self.studio_status); v.addWidget(self.studio_plan,1)
        self.studio_followup=QLineEdit(); self.studio_followup.setPlaceholderText("Follow-up: e.g. eyes friendlier, less glow")
        send=QPushButton("SEND"); send.clicked.connect(self.studio_followup_send)
        fr=QHBoxLayout(); fr.addWidget(self.studio_followup,1); fr.addWidget(send); v.addLayout(fr)
        saved=CONTROL.load(); self.studio_provider.setCurrentText(saved.get("ai_provider","triforce")); self.studio_model.setText(saved.get("ai_model","") or "")
        self._studio_job_id=None
        return w

    def _studio_refresh_job(self):
        if not self._studio_job_id: return
        try:
            from gimp_mcp.server import jobs
            job=jobs.get(self._studio_job_id)
            self.studio_status.setText(f"Job {job.job_id[:8]} · {job.status} · step {job.current_step} · {job.progress:.1f}%")
            self.studio_plan.setPlainText(json.dumps(job.plan or {"status":job.status,"error":job.error},indent=2,ensure_ascii=False))
        except Exception as exc:
            self.studio_status.setText(str(exc))

    def studio_run(self):
        prompt=self.studio_prompt.toPlainText().strip()
        if not prompt: return
        try:
            from gimp_mcp.server import jobs, _prompt_runner
            job=jobs.create(prompt,self.studio_provider.currentText(),self.studio_model.text().strip(),self.studio_mode.currentText())
            self._studio_job_id=job.job_id
            import threading
            threading.Thread(target=lambda: _prompt_runner().run(job),daemon=True).start()
            self._studio_refresh_job()
        except Exception as exc: self.studio_status.setText(f"Run failed: {exc}")

    def studio_pause(self):
        if not self._studio_job_id: return
        from gimp_mcp.server import jobs
        jobs.pause(self._studio_job_id); self._studio_refresh_job()

    def studio_stop(self):
        if not self._studio_job_id: return
        from gimp_mcp.server import jobs
        jobs.cancel(self._studio_job_id); self._studio_refresh_job()

    def studio_undo(self):
        if not self._studio_job_id: return
        try:
            from gimp_mcp.server import jobs, session_undo
            job=jobs.get(self._studio_job_id)
            if job.session_id: session_undo(job.session_id)
        finally: self._studio_refresh_job()

    def studio_redo(self):
        if not self._studio_job_id: return
        try:
            from gimp_mcp.server import jobs, session_redo
            job=jobs.get(self._studio_job_id)
            if job.session_id: session_redo(job.session_id)
        finally: self._studio_refresh_job()

    def studio_followup_send(self):
        text=self.studio_followup.text().strip()
        if not self._studio_job_id or not text: return
        from gimp_mcp.server import jobs, _prompt_runner
        job=jobs.get(self._studio_job_id)
        import threading
        threading.Thread(target=lambda: _prompt_runner().followup(job,text),daemon=True).start()
        self.studio_followup.clear()

    def studio_cooler(self):
        if not self._studio_job_id: return
        from gimp_mcp.server import jobs, _prompt_runner
        job=jobs.get(self._studio_job_id)
        import threading
        threading.Thread(target=lambda: _prompt_runner().make_it_cooler(job),daemon=True).start()

    def _live_tab(self):
        w=QWidget(); outer=QHBoxLayout(w)
        left=QVBoxLayout(); right=QVBoxLayout()
        self.sessions=QListWidget(); self.sessions.currentTextChanged.connect(self._session_changed)
        self.log=QTextEdit(); self.log.setReadOnly(True)
        self.live_bridge_label=QLabel('GIMP Live Bridge: checking...')
        left.addWidget(self.live_bridge_label); left.addWidget(QLabel("Artwork sessions")); left.addWidget(self.sessions,1); left.addWidget(QLabel("Live operations")); left.addWidget(self.log,2)
        self.preview=QLabel("No preview yet"); self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.preview.setMinimumSize(520,420)
        right.addWidget(QLabel("Live preview")); right.addWidget(self.preview,1)
        outer.addLayout(left,1); outer.addLayout(right,2); return w

    def _server_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        self.server_status=QLabel(); row=QHBoxLayout();
        start=QPushButton("Start MCP Server"); stop=QPushButton("Stop MCP Server"); start.clicked.connect(self.start_server); stop.clicked.connect(self.stop_server)
        live_install=QPushButton('Install / Update GIMP Live Plug-in'); live_install.clicked.connect(self.install_live_plugin)
        row.addWidget(start); row.addWidget(stop); row.addWidget(live_install); row.addStretch()
        self.endpoint=QLineEdit("http://127.0.0.1:8000/mcp"); self.endpoint.setReadOnly(True)
        v.addWidget(self.server_status); v.addLayout(row); v.addWidget(QLabel("Streamable HTTP endpoint")); v.addWidget(self.endpoint)
        v.addWidget(QLabel('Live GIMP mode: install the plug-in once, then restart GIMP. When connected, successful MCP edits are mirrored into the visible GIMP display.'))
        v.addStretch(); return w

    def _ai_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        v.addWidget(QLabel("Choose an optional AI art director. Provider credentials stay with official clients/AICoder."))
        self.provider=QComboBox(); self.provider.addItems(["triforce","chatgpt","claude","gemini","mistral"])
        self.model=QComboBox(); self.model.setMinimumWidth(420)
        saved=CONTROL.load(); idx=self.provider.findText(saved.get("ai_provider","triforce")); self.provider.setCurrentIndex(max(0,idx))
        row=QHBoxLayout(); row.addWidget(QLabel("Provider")); row.addWidget(self.provider); row.addWidget(QLabel("Model")); row.addWidget(self.model)
        status=QPushButton("Refresh models/status"); connect=QPushButton("Connect / Login"); save=QPushButton("Use selected model")
        status.clicked.connect(self.provider_status); connect.clicked.connect(self.provider_connect); save.clicked.connect(self.save_ai_selection)
        row.addWidget(status); row.addWidget(connect); row.addWidget(save); row.addStretch(); v.addLayout(row)
        self.provider_output=QTextEdit(); self.provider_output.setReadOnly(True); v.addWidget(self.provider_output,1)
        QTimer.singleShot(100, self.provider_status)
        return w


    def _setup_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        v.addWidget(QLabel("One-shot setup, repair and update checks. System package installation always asks through PolicyKit."))
        row=QHBoxLayout()
        check=QPushButton("Check system / updates"); install=QPushButton("Install / repair dependencies")
        sync=QPushButton("Sync project"); upgrade=QPushButton("Upgrade project deps")
        uvup=QPushButton("Update uv"); test=QPushButton("Run self-test")
        check.clicked.connect(self.setup_check); install.clicked.connect(self.setup_install)
        sync.clicked.connect(lambda: self.setup_sync(False)); upgrade.clicked.connect(lambda: self.setup_sync(True))
        uvup.clicked.connect(self.setup_uv_update); test.clicked.connect(self.setup_self_test)
        for b in (check,install,sync,upgrade,uvup,test): row.addWidget(b)
        row.addStretch(); v.addLayout(row)
        self.setup_output=QTextEdit(); self.setup_output.setReadOnly(True); v.addWidget(self.setup_output,1)
        return w

    def _setup_manager(self):
        return SetupManager(Path.home()/"gimp-mcp")

    def setup_check(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            data=self._setup_manager().update_check(); self.setup_output.setPlainText(json.dumps(data,indent=2,default=str))
        except Exception as exc: self.setup_output.setPlainText(f"Update check failed: {exc}")
        finally: QApplication.restoreOverrideCursor()

    def setup_install(self):
        answer=QMessageBox.question(self,"Install system dependencies","Install/repair GIMP, GI, PyQt6, venv, git and curl using APT? PolicyKit will ask for authorization.")
        if answer != QMessageBox.StandardButton.Yes: return
        try:
            r=self._setup_manager().install_system_dependencies(); self.setup_output.setPlainText((r.stdout or "")+(r.stderr or ""))
        except Exception as exc: self.setup_output.setPlainText(str(exc))

    def setup_sync(self, upgrade=False):
        if upgrade:
            answer=QMessageBox.question(self,"Upgrade project dependencies","Resolve newer project dependency versions and rewrite uv.lock, then sync? This may change runtime behavior.")
            if answer != QMessageBox.StandardButton.Yes: return
        try:
            r=self._setup_manager().sync_project(upgrade=upgrade); self.setup_output.setPlainText((r.stdout or "")+(r.stderr or ""))
        except Exception as exc: self.setup_output.setPlainText(str(exc))

    def setup_uv_update(self):
        try:
            r=self._setup_manager().update_uv(); self.setup_output.setPlainText((r.stdout or "")+(r.stderr or ""))
        except Exception as exc: self.setup_output.setPlainText(str(exc))

    def setup_self_test(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try: self.setup_output.setPlainText(json.dumps(self._setup_manager().self_test(),indent=2,default=str))
        except Exception as exc: self.setup_output.setPlainText(str(exc))
        finally: QApplication.restoreOverrideCursor()

    def _settings_tab(self):
        w=QWidget(); form=QFormLayout(w); saved=CONTROL.load()
        self.preview_interval=QSpinBox(); self.preview_interval.setRange(250,5000); self.preview_interval.setValue(int(saved.get("preview_interval_ms",750)))
        self.preview_size=QSpinBox(); self.preview_size.setRange(256,3000); self.preview_size.setValue(int(saved.get("preview_max",1200)))
        save=QPushButton("Save settings"); save.clicked.connect(self.save_settings)
        form.addRow("Live preview interval (ms)", self.preview_interval); form.addRow("Preview max size", self.preview_size); form.addRow(save)
        return w

    def save_settings(self):
        data=CONTROL.load(); data.update({"preview_interval_ms":self.preview_interval.value(),"preview_max":self.preview_size.value()}); CONTROL.save(data); self.timer.setInterval(self.preview_interval.value())

    def save_ai_selection(self):
        data=CONTROL.load(); data["ai_provider"]=self.provider.currentText(); data["ai_model"]=self.model.currentData() or self.model.currentText(); CONTROL.save(data); self.provider_output.append(f"Selected: {data['ai_provider']} / {data['ai_model']}")

    def _server_pid(self):
        try:
            pid=int(PIDFILE.read_text().strip()); os.kill(pid,0); return pid
        except Exception: return None

    def start_server(self):
        if self._server_pid(): return
        cmd=["uv","run","python","-c","from gimp_mcp.server import mcp; mcp.run(transport='streamable-http')"]
        log=(STATE/"server.log").open("ab")
        p=subprocess.Popen(cmd,cwd=str(Path.home()/"gimp-mcp"),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        PIDFILE.write_text(str(p.pid)); self.refresh()

    def stop_server(self):
        pid=self._server_pid()
        if pid:
            try: os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError: pass
        PIDFILE.unlink(missing_ok=True); self.refresh()

    def install_live_plugin(self):
        script=Path.home()/"gimp-mcp/scripts/install-gimp-live-plugin"
        try:
            r=subprocess.run([str(script)],capture_output=True,text=True,timeout=30,check=False)
            text=((r.stdout or '')+(r.stderr or '')).strip()
            QMessageBox.information(self,'GIMP Live Plug-in',text or 'Installation finished. Restart GIMP once.')
        except Exception as exc:
            QMessageBox.critical(self,'GIMP Live Plug-in',str(exc))

    def provider_status(self):
        provider=self.provider.currentText()
        if provider == "triforce":
            code="from gimp_mcp.ai_control import AICoderAdapter; import json; a=AICoderAdapter(); print(json.dumps({'status':a.triforce_status(),'models':a.triforce_models()}, default=str))"
        else:
            code=f"from gimp_mcp.ai_control import AICoderAdapter; import json; a=AICoderAdapter(); print(json.dumps({{'status':[x for x in a.providers() if x.get('provider')=='{provider}'],'models':a.models('{provider}')}}, default=str))"
        r=subprocess.run(["uv","run","python","-c",code],cwd=str(Path.home()/"gimp-mcp"),capture_output=True,text=True,timeout=45)
        text=(r.stdout or r.stderr).strip(); self.provider_output.setPlainText(text); self.model.clear()
        try:
            payload=json.loads(r.stdout); models=payload.get("models") or []
            for item in models:
                if isinstance(item,dict):
                    mid=str(item.get("id") or item.get("model") or item.get("name") or ""); label=str(item.get("display_name") or item.get("name") or mid); self.model.addItem(label,mid)
                else: self.model.addItem(str(item),str(item))
            wanted=CONTROL.load().get("ai_model","")
            for i in range(self.model.count()):
                if self.model.itemData(i)==wanted: self.model.setCurrentIndex(i); break
        except Exception: pass

    def provider_connect(self):
        provider=self.provider.currentText()
        if provider == "triforce":
            self.provider_output.setPlainText("AILinux/TriForce login is shared with AICoder. Use AICoder login/setup, then refresh here."); return
        code=f"from gimp_mcp.ai_control import AICoderAdapter; import json; print(json.dumps(AICoderAdapter().connect('{provider}', open_browser=True), default=str))"
        subprocess.Popen(["uv","run","python","-c",code],cwd=str(Path.home()/"gimp-mcp"))
        self.provider_output.setPlainText(f"Started official {provider} login flow. Complete it in the opened browser/terminal.")

    def _session_changed(self, sid):
        self._load_preview(sid)

    def _load_preview(self, sid):
        path=SESSIONS/sid/"preview.png"
        if path.exists():
            pix=QPixmap(str(path)); self.preview.setPixmap(pix.scaled(self.preview.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        else: self.preview.setText("No preview yet for this session")

    def refresh(self):
        pid=self._server_pid(); self.server_status.setText(f"Server: {'RUNNING pid='+str(pid) if pid else 'STOPPED'}")
        live=LiveBridge(timeout=0.25).status()
        if live.get('ok'):
            self.live_bridge_label.setText(f"GIMP Live Bridge: CONNECTED · GIMP {live.get('gimp_version','?')}")
        else:
            self.live_bridge_label.setText('GIMP Live Bridge: OFFLINE · batch/preview mode')
        current=self.sessions.currentItem().text() if self.sessions.currentItem() else ""
        ids=sorted([p.name for p in SESSIONS.iterdir() if p.is_dir() and (p/'document.xcf').exists()]) if SESSIONS.exists() else []
        if [self.sessions.item(i).text() for i in range(self.sessions.count())] != ids:
            self.sessions.clear(); self.sessions.addItems(ids)
            if current in ids: self.sessions.setCurrentRow(ids.index(current))
            elif ids: self.sessions.setCurrentRow(len(ids)-1)
        if EVENTS.exists():
            lines=EVENTS.read_text(encoding="utf-8",errors="replace").splitlines()[-80:]
            pretty=[]
            for line in lines:
                try:
                    e=json.loads(line); pretty.append(f"{e.get('type')}  {e.get('operation') or ''}  {e.get('session_id') or ''}  {e.get('status') or ''}  {e.get('duration_ms') or ''}")
                except Exception: pass
            self.log.setPlainText("\n".join(pretty)); self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        if self.sessions.currentItem(): self._load_preview(self.sessions.currentItem().text())
        self._studio_refresh_job()


def main():
    app=QApplication(sys.argv); win=ControlCenter(); win.show(); return app.exec()

if __name__ == "__main__": raise SystemExit(main())
