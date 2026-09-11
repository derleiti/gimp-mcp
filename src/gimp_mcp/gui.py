from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
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
ROOT = Path(os.getenv("GIMP_MCP_ROOT", str(Path.home() / "gimp-mcp"))).resolve()
PROJECT_PYTHON = ROOT / ".venv/bin/python"
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
        self.studio_provider=QComboBox(); self.studio_provider.addItems(["chatgpt","claude","gemini","mistral","triforce"])
        self.studio_model=QComboBox(); self.studio_model.setEditable(True); self.studio_model.setMinimumWidth(420); self.studio_model.setPlaceholderText("model id")
        self.studio_provider.currentTextChanged.connect(self.studio_refresh_models)
        self.studio_mode=QComboBox(); self.studio_mode.addItems(["auto","live","batch"])
        top.addWidget(QLabel("Provider")); top.addWidget(self.studio_provider); top.addWidget(QLabel("Model")); top.addWidget(self.studio_model,1); top.addWidget(QLabel("Mode")); top.addWidget(self.studio_mode)
        v.addLayout(top)
        self.studio_prompt=QTextEdit(); self.studio_prompt.setPlaceholderText("Describe the artwork or edit you want GIMP to perform... (Ctrl+Enter to run)")
        run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.studio_prompt)
        run_shortcut.activated.connect(self.studio_run)
        run_shortcut_keypad = QShortcut(QKeySequence("Ctrl+Enter"), self.studio_prompt)
        run_shortcut_keypad.activated.connect(self.studio_run)
        self._studio_shortcuts = (run_shortcut, run_shortcut_keypad)
        v.addWidget(QLabel("Prompt")); v.addWidget(self.studio_prompt,1)
        row=QHBoxLayout()
        run=QPushButton("RUN"); pause=QPushButton("PAUSE"); stop=QPushButton("STOP"); undo=QPushButton("UNDO"); redo=QPushButton("REDO"); cooler=QPushButton("MAKE IT COOLER")
        run.clicked.connect(self.studio_run); pause.clicked.connect(self.studio_pause); stop.clicked.connect(self.studio_stop); undo.clicked.connect(self.studio_undo); redo.clicked.connect(self.studio_redo); cooler.clicked.connect(self.studio_cooler)
        for b in (run,pause,stop,undo,redo,cooler): row.addWidget(b)
        row.addStretch(); v.addLayout(row)
        self.studio_status=QLabel("No active job")
        self.studio_plan=QTextEdit(); self.studio_plan.setReadOnly(True)
        v.addWidget(self.studio_status); v.addWidget(self.studio_plan,1)
        self.studio_followup=QLineEdit()
        self.studio_followup.setPlaceholderText("Quick prompt / follow-up — Enter sends. With no job this starts a new artwork.")
        self.studio_followup.returnPressed.connect(self.studio_followup_send)
        send=QPushButton("SEND"); send.clicked.connect(self.studio_followup_send)
        fr=QHBoxLayout(); fr.addWidget(QLabel("Quick prompt / follow-up")); fr.addWidget(self.studio_followup,1); fr.addWidget(send); v.addLayout(fr)
        saved=CONTROL.load(); self.studio_provider.setCurrentText(saved.get("ai_provider","chatgpt")); self._studio_job_id=None
        QTimer.singleShot(150, self.studio_refresh_models)
        return w

    def _select_studio_model(self, wanted):
        wanted=str(wanted or "")
        for i in range(self.studio_model.count()):
            if str(self.studio_model.itemData(i) or "") == wanted or self.studio_model.itemText(i) == wanted:
                self.studio_model.setCurrentIndex(i); return
        if wanted:
            self.studio_model.setEditText(wanted)

    def studio_refresh_models(self, *_args):
        if not PROJECT_PYTHON.exists(): return
        provider=self.studio_provider.currentText()
        proc=QProcess(self); proc.setWorkingDirectory(str(ROOT)); proc.setProgram(str(PROJECT_PYTHON)); proc.setArguments(["-m","gimp_mcp.provider_cli","models",provider])
        proc.finished.connect(lambda _code,_status,p=proc: self._studio_models_finished(p))
        self._studio_models_process=proc; proc.start()

    def _studio_models_finished(self, proc):
        stdout=bytes(proc.readAllStandardOutput()).decode("utf-8","replace")
        wanted=CONTROL.load().get("ai_model","")
        current=str(self.studio_model.currentData() or self.studio_model.currentText())
        self.studio_model.clear()
        try:
            rows=json.loads(stdout)
            for item in rows if isinstance(rows,list) else []:
                if isinstance(item,dict):
                    mid=str(item.get("id") or item.get("model") or item.get("name") or "")
                    label=str(item.get("display") or item.get("displayName") or item.get("name") or mid)
                    if mid: self.studio_model.addItem(label,mid)
                else: self.studio_model.addItem(str(item),str(item))
        except Exception: pass
        self._select_studio_model(current or wanted)
        if getattr(self, "_studio_models_process", None) is proc:
            self._studio_models_process = None
        proc.deleteLater()

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
            job=jobs.create(prompt,self.studio_provider.currentText(),str(self.studio_model.currentData() or self.studio_model.currentText()).strip(),self.studio_mode.currentText())
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
        if not text:
            return
        self.studio_followup.clear()
        if not self._studio_job_id:
            # The compact input doubles as the initial prompt. Previously SEND
            # silently did nothing until a job already existed.
            self.studio_prompt.setPlainText(text)
            self.studio_run()
            return
        try:
            from gimp_mcp.server import jobs, _prompt_runner
            job=jobs.get(self._studio_job_id)
        except Exception as exc:
            self.studio_status.setText(f"Follow-up failed: {exc}")
            return
        import threading
        threading.Thread(target=lambda: _prompt_runner().followup(job,text),daemon=True).start()
        self.studio_status.setText(f"Follow-up queued for job {job.job_id[:8]}…")

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
        copy_endpoint=QPushButton("Copy endpoint"); copy_endpoint.clicked.connect(lambda: QApplication.clipboard().setText(self.endpoint.text()))
        endpoint_row=QHBoxLayout(); endpoint_row.addWidget(self.endpoint,1); endpoint_row.addWidget(copy_endpoint)
        v.addWidget(self.server_status); v.addLayout(row); v.addWidget(QLabel("Streamable HTTP endpoint (for MCP clients — not a browser chat page)")); v.addLayout(endpoint_row)
        v.addWidget(QLabel('Opening /mcp directly in a browser may show "Missing session ID"; that is expected because a browser GET does not perform the MCP initialize handshake.'))
        v.addWidget(QLabel('Live GIMP mode: install the plug-in once, then restart GIMP. When connected, successful MCP edits are mirrored into the visible GIMP display.'))
        v.addStretch(); return w

    def _ai_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        v.addWidget(QLabel("Choose an optional AI art director. Provider credentials stay with the official provider clients; GIMP MCP does not depend on AICoder."))
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
        return SetupManager(ROOT)

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
            pid=int(PIDFILE.read_text().strip())
            os.kill(pid,0)
            cmdline=Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if "gimp_mcp.server" not in cmdline:
                PIDFILE.unlink(missing_ok=True)
                return None
            return pid
        except Exception:
            PIDFILE.unlink(missing_ok=True)
            return None

    def start_server(self):
        if self._server_pid(): return
        if not PROJECT_PYTHON.exists():
            QMessageBox.critical(self,"MCP Server","Project environment is missing. Run uv sync first.")
            return
        cmd=[str(PROJECT_PYTHON),"-c","from gimp_mcp.server import mcp; mcp.run(transport='streamable-http')"]
        log=(STATE/"server.log").open("ab")
        p=subprocess.Popen(cmd,cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        PIDFILE.write_text(str(p.pid)); self.refresh()

    def stop_server(self):
        pid=self._server_pid()
        if pid:
            try: os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError: pass
        PIDFILE.unlink(missing_ok=True); self.refresh()

    def install_live_plugin(self):
        script=ROOT/"scripts/install-gimp-live-plugin"
        try:
            r=subprocess.run([str(script)],capture_output=True,text=True,timeout=30,check=False)
            text=((r.stdout or '')+(r.stderr or '')).strip()
            QMessageBox.information(self,'GIMP Live Plug-in',text or 'Installation finished. Restart GIMP once.')
        except Exception as exc:
            QMessageBox.critical(self,'GIMP Live Plug-in',str(exc))

    def provider_status(self):
        provider=self.provider.currentText()
        if not PROJECT_PYTHON.exists():
            self.provider_output.setPlainText("Project environment missing; run Sync project first.")
            return
        self.provider_output.setPlainText(f"Checking {provider}…")
        proc=QProcess(self)
        proc.setWorkingDirectory(str(ROOT))
        proc.setProgram(str(PROJECT_PYTHON))
        proc.setArguments(["-m","gimp_mcp.provider_cli","status",provider])
        proc.finished.connect(lambda _code,_status,p=proc: self._provider_status_finished(p))
        self._provider_process=proc
        proc.start()

    def _provider_status_finished(self, proc):
        stdout=bytes(proc.readAllStandardOutput()).decode("utf-8","replace")
        stderr=bytes(proc.readAllStandardError()).decode("utf-8","replace")
        text=(stdout or stderr).strip(); self.provider_output.setPlainText(text); self.model.clear()
        try:
            payload=json.loads(stdout); models=payload.get("models") or []
            for item in models:
                if isinstance(item,dict):
                    mid=str(item.get("id") or item.get("model") or item.get("name") or ""); label=str(item.get("display") or item.get("display_name") or item.get("name") or mid); self.model.addItem(label,mid)
                else: self.model.addItem(str(item),str(item))
            wanted=CONTROL.load().get("ai_model","")
            for i in range(self.model.count()):
                if self.model.itemData(i)==wanted: self.model.setCurrentIndex(i); break
        except Exception:
            if not text: self.provider_output.setPlainText("Provider check returned no usable data.")
        proc.deleteLater()

    def provider_connect(self):
        provider=self.provider.currentText()
        if provider == "triforce":
            self.provider_output.setPlainText("TriForce is independent too. Configure GIMP_MCP_TRIFORCE_TOKEN, then refresh here."); return
        if not PROJECT_PYTHON.exists():
            self.provider_output.setPlainText("Project environment missing; run Sync project first.")
            return
        subprocess.Popen([str(PROJECT_PYTHON),"-m","gimp_mcp.provider_cli","connect",provider],cwd=str(ROOT),start_new_session=True)
        self.provider_output.setPlainText(f"Started native {provider} login flow in the official client. Complete it, then refresh models/status.")

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
