from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from gimp_mcp.control_settings import ControlSettings
from gimp_mcp.live_bridge import LiveBridge
from gimp_mcp.setup_manager import SetupManager
from gimp_mcp.first_run import SetupWizard

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget
)

STATE = Path(os.getenv("GIMP_MCP_STATE_DIR", str(Path.home() / ".local/state/gimp-mcp")))
EVENTS = STATE / "events.jsonl"
SESSIONS = Path(os.getenv("GIMP_MCP_SESSION_ROOT", "/tmp/gimp-mcp/sessions"))
PIDFILE = STATE / "server.pid"
ROOT = Path(os.getenv("GIMP_MCP_ROOT", str(Path.home() / "gimp-mcp"))).resolve()
PROJECT_PYTHON = ROOT / ".venv/bin/python"
CONTROL = ControlSettings(STATE / "control.json")

GUI_LOG = STATE / "gui.log"

def _configure_gui_logging() -> logging.Logger:
    logger=logging.getLogger("gimp_mcp.gui")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler=RotatingFileHandler(GUI_LOG,maxBytes=1_000_000,backupCount=3,encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate=False
    return logger

def _prefer_active_session(active_session_id: str | None, selected_session_id: str | None) -> str | None:
    active = str(active_session_id or "").strip()
    selected = str(selected_session_id or "").strip()
    return active or selected or None

GUI_LOGGER = _configure_gui_logging()


class ControlCenter(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GIMP MCP Control Center")
        self.resize(1180, 760)
        STATE.mkdir(parents=True, exist_ok=True)
        GUI_LOGGER.info("Control Center starting pid=%s root=%s sessions=%s", os.getpid(), ROOT, SESSIONS)
        tabs = QTabWidget(); self.tabs=tabs; self.setCentralWidget(tabs)
        tabs.addTab(self._studio_tab(), "Studio")
        tabs.addTab(self._live_tab(), "Live")
        tabs.addTab(self._server_tab(), "Server")
        tabs.addTab(self._ai_tab(), "AI Control")
        tabs.addTab(self._setup_tab(), "Setup / Updates")
        tabs.addTab(self._settings_tab(), "Settings")
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(750)
        self.refresh()
        QTimer.singleShot(350, self._maybe_first_run)
        QTimer.singleShot(900, self._startup_update_check)

    def _maybe_first_run(self):
        if not CONTROL.load().get("first_run_complete", False):
            self.open_setup_wizard()

    def open_setup_wizard(self):
        wizard=SetupWizard(CONTROL, ROOT, self)
        if wizard.exec():
            saved=CONTROL.load()
            self.timer.setInterval(int(saved.get("preview_interval_ms", 750)))
            self._apply_runtime_settings(saved)

    def _apply_runtime_settings(self, saved=None):
        saved=saved or CONTROL.load()
        if saved.get("gimp_runtime") == "managed" and saved.get("managed_gimp_path"):
            os.environ["GIMP_MCP_GIMP"] = str(saved["managed_gimp_path"])
        else:
            os.environ.pop("GIMP_MCP_GIMP", None)
        port=int(saved.get("mcp_port",8000))
        os.environ["GIMP_MCP_ENDPOINT"] = f"http://127.0.0.1:{port}/mcp"
        if saved.get("mcp_network_enabled") and saved.get("mcp_auth_token"):
            os.environ["GIMP_MCP_AUTH_TOKEN"] = str(saved["mcp_auth_token"])
        else:
            os.environ.pop("GIMP_MCP_AUTH_TOKEN", None)

    def _startup_update_check(self):
        saved=CONTROL.load()
        if not saved.get("first_run_complete") or not saved.get("check_updates_on_start", True) or not PROJECT_PYTHON.exists():
            return
        proc=QProcess(self); proc.setWorkingDirectory(str(ROOT)); proc.setProgram(str(PROJECT_PYTHON)); proc.setArguments(["-m","gimp_mcp.setup_cli","status"])
        proc.finished.connect(lambda _code,_status,p=proc:self._startup_update_finished(p))
        self._startup_update_process=proc; proc.start()

    def _startup_update_finished(self, proc):
        text=bytes(proc.readAllStandardOutput()).decode("utf-8","replace")
        try:
            data=json.loads(text); gimp=data.get("gimp") or {}; gm=data.get("gimp_mcp") or {}
            notes=[]
            if gimp.get("update_available"):
                notes.append(f"GIMP {gimp.get('latest_version')} is available (current: {gimp.get('managed_version') or gimp.get('system_version') or 'unknown'}).")
            if int(gm.get("behind") or 0) > 0:
                notes.append(f"GIMP MCP has {gm.get('behind')} update(s) available from the verified GitHub origin.")
            if notes:
                self.statusBar().showMessage("  ".join(notes), 15000)
                if hasattr(self,"setup_output"):
                    self.setup_output.setPlainText("Update check:\n"+"\n".join(notes))
        except Exception as exc:
            GUI_LOGGER.warning("Startup update check failed: %s", exc)
        if getattr(self,"_startup_update_process",None) is proc: self._startup_update_process=None
        proc.deleteLater()

    def _studio_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        top=QHBoxLayout()
        self.studio_provider=QComboBox(); self.studio_provider.addItems(["chatgpt","claude","gemini","mistral","triforce"])
        self.studio_model=QComboBox(); self.studio_model.setEditable(True); self.studio_model.setMinimumWidth(420); self.studio_model.setPlaceholderText("model id")
        self.studio_provider.currentTextChanged.connect(self.studio_refresh_models)
        self.studio_mode=QComboBox(); self.studio_mode.addItems(["auto","live","batch"])
        top.addWidget(QLabel("Provider")); top.addWidget(self.studio_provider); top.addWidget(QLabel("Model")); top.addWidget(self.studio_model,1); top.addWidget(QLabel("Mode")); top.addWidget(self.studio_mode)
        v.addLayout(top)
        self.studio_prompt=QTextEdit(); self.studio_prompt.setPlaceholderText("Describe naturally what you want GIMP to create or change… e.g. ‘Create a cute brown bear named Brumo on a light background.’ (Ctrl+Enter to run)")
        run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.studio_prompt)
        run_shortcut.activated.connect(self.studio_run)
        run_shortcut_keypad = QShortcut(QKeySequence("Ctrl+Enter"), self.studio_prompt)
        run_shortcut_keypad.activated.connect(self.studio_run)
        self._studio_shortcuts = (run_shortcut, run_shortcut_keypad)
        v.addWidget(QLabel("Prompt")); v.addWidget(self.studio_prompt,1)
        row=QHBoxLayout()
        run=QPushButton("RUN"); pause=QPushButton("PAUSE"); stop=QPushButton("STOP"); undo=QPushButton("UNDO"); redo=QPushButton("REDO"); cooler=QPushButton("MAKE IT COOLER")
        exp_xcf=QPushButton("XCF"); exp_png=QPushButton("PNG"); exp_jpg=QPushButton("JPG")
        run.clicked.connect(self.studio_run); pause.clicked.connect(self.studio_pause); stop.clicked.connect(self.studio_stop); undo.clicked.connect(self.studio_undo); redo.clicked.connect(self.studio_redo); cooler.clicked.connect(self.studio_cooler)
        exp_xcf.clicked.connect(lambda: self.export_selected_artwork("xcf")); exp_png.clicked.connect(lambda: self.export_selected_artwork("png")); exp_jpg.clicked.connect(lambda: self.export_selected_artwork("jpg"))
        for b in (run,pause,stop,undo,redo,cooler,exp_xcf,exp_png,exp_jpg): row.addWidget(b)
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
        # Clear immediately so a previous provider model cannot be submitted
        # while the asynchronous catalogue refresh is still running.
        self.studio_model.clear()
        self.studio_model.clearEditText()
        proc=QProcess(self); proc.setProperty("provider", provider)
        proc.setWorkingDirectory(str(ROOT)); proc.setProgram(str(PROJECT_PYTHON)); proc.setArguments(["-m","gimp_mcp.provider_cli","models",provider])
        proc.finished.connect(lambda _code,_status,p=proc: self._studio_models_finished(p))
        self._studio_models_process=proc; proc.start()

    @staticmethod
    def _model_matches_provider(provider, model):
        model=str(model or "").strip()
        return not model.startswith("account:") or model.startswith(f"account:{provider}/")

    def _studio_models_finished(self, proc):
        provider=str(proc.property("provider") or "")
        stdout=bytes(proc.readAllStandardOutput()).decode("utf-8","replace")
        if provider != self.studio_provider.currentText():
            proc.deleteLater(); return
        wanted=CONTROL.load().get("ai_model","")
        current=str(self.studio_model.currentData() or self.studio_model.currentText())
        if not self._model_matches_provider(provider, current): current=""
        if not self._model_matches_provider(provider, wanted): wanted=""
        self.studio_model.clear()
        try:
            rows=json.loads(stdout)
            for item in rows if isinstance(rows,list) else []:
                if isinstance(item,dict):
                    mid=str(item.get("id") or item.get("model") or item.get("name") or "")
                    label=str(item.get("display") or item.get("displayName") or item.get("name") or mid)
                    if mid: self.studio_model.addItem(label,mid)
                else: self.studio_model.addItem(str(item),str(item))
        except Exception as exc:
            GUI_LOGGER.warning("Studio model catalog parse failed provider=%s error=%s stderr=%s", provider, exc, bytes(proc.readAllStandardError()).decode("utf-8","replace")[-500:])
        preferred=current or wanted
        if preferred:
            self._select_studio_model(preferred)
        elif self.studio_model.count():
            self.studio_model.setCurrentIndex(0)
        if getattr(self, "_studio_models_process", None) is proc:
            self._studio_models_process = None
        proc.deleteLater()

    def _studio_refresh_job(self):
        if not self._studio_job_id: return
        try:
            from gimp_mcp.server import jobs
            job=jobs.get(self._studio_job_id)
            self.studio_status.setText(f"Job {job.job_id[:8]} · {job.status} · step {job.current_step} · {job.progress:.1f}%" + (f" · {job.error}" if job.error else ""))
            if job.plan and isinstance(job.plan.get("steps"), list):
                lines=[f"Goal: {job.plan.get('goal','')}", ""]
                for i, step in enumerate(job.plan["steps"], 1):
                    state=str(step.get("status") or "pending").upper()
                    lines.append(f"[{state}] {i}. {step.get('tool','')} — {step.get('reason','')}")
                    if step.get("error"): lines.append(f"    ERROR: {step['error']}")
                self.studio_plan.setPlainText("\n".join(lines))
            else:
                self.studio_plan.setPlainText(json.dumps({"status":job.status,"error":job.error},indent=2,ensure_ascii=False))
        except Exception as exc:
            self.studio_status.setText(str(exc))

    def studio_run(self):
        prompt=self.studio_prompt.toPlainText().strip()
        if not prompt: return
        try:
            from gimp_mcp.server import jobs, _mcp_prompt_runner
            if not self._server_pid():
                self.start_server()
            job=jobs.create(prompt,self.studio_provider.currentText(),str(self.studio_model.currentData() or self.studio_model.currentText()).strip(),self.studio_mode.currentText())
            self._studio_job_id=job.job_id
            import threading
            threading.Thread(target=lambda: _mcp_prompt_runner().run(job),daemon=True).start()
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
            from gimp_mcp.server import jobs, _mcp_prompt_runner
            job=jobs.get(self._studio_job_id)
        except Exception as exc:
            self.studio_status.setText(f"Follow-up failed: {exc}")
            return
        import threading
        threading.Thread(target=lambda: _mcp_prompt_runner().followup(job,text),daemon=True).start()
        self.studio_status.setText(f"Follow-up queued for job {job.job_id[:8]}…")

    def studio_cooler(self):
        if not self._studio_job_id: return
        from gimp_mcp.server import jobs, _mcp_prompt_runner
        job=jobs.get(self._studio_job_id)
        import threading
        threading.Thread(target=lambda: _mcp_prompt_runner().make_it_cooler(job),daemon=True).start()

    def _live_tab(self):
        w=QWidget(); outer=QHBoxLayout(w)
        left=QVBoxLayout(); right=QVBoxLayout()
        self.sessions=QListWidget(); self.sessions.currentTextChanged.connect(self._session_changed)
        self.log=QTextEdit(); self.log.setReadOnly(True)
        self.live_bridge_label=QLabel('GIMP Live Bridge: checking...')
        self.follow_active_session=QCheckBox("Follow active Studio job")
        self.follow_active_session.setChecked(True)
        self.sessions.itemClicked.connect(lambda _item: self.follow_active_session.setChecked(False))
        left.addWidget(self.live_bridge_label); left.addWidget(self.follow_active_session); left.addWidget(QLabel("Artwork sessions")); left.addWidget(self.sessions,1); left.addWidget(QLabel("Live operations")); left.addWidget(self.log,2)
        self.preview=QLabel("No preview yet")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(520,420)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("QLabel { border: 1px solid palette(mid); background: palette(base); }")
        self._preview_pixmap=None
        self._preview_signature=None
        self._preview_session_id=None
        self.preview_meta=QLabel("Session: - · Preview: -")
        self.preview_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        export_row=QHBoxLayout()
        export_xcf=QPushButton("EXPORT XCF"); export_png=QPushButton("EXPORT PNG"); export_jpg=QPushButton("EXPORT JPG")
        export_xcf.clicked.connect(lambda: self.export_selected_artwork("xcf"))
        export_png.clicked.connect(lambda: self.export_selected_artwork("png"))
        export_jpg.clicked.connect(lambda: self.export_selected_artwork("jpg"))
        for b in (export_xcf,export_png,export_jpg): export_row.addWidget(b)
        export_row.addStretch()
        right.addWidget(QLabel("Live preview")); right.addWidget(self.preview,1); right.addWidget(self.preview_meta); right.addLayout(export_row)
        outer.addLayout(left,1); outer.addLayout(right,2); return w

    def _server_tab(self):
        w=QWidget(); v=QVBoxLayout(w)
        self.server_status=QLabel(); row=QHBoxLayout();
        start=QPushButton("Start MCP Server"); stop=QPushButton("Stop MCP Server"); start.clicked.connect(self.start_server); stop.clicked.connect(self.stop_server)
        live_install=QPushButton('Install / Update GIMP Live Plug-in'); live_install.clicked.connect(self.install_live_plugin)
        row.addWidget(start); row.addWidget(stop); row.addWidget(live_install); row.addStretch()
        saved=CONTROL.load(); initial_host=str(saved.get("mcp_bind_host","127.0.0.1")); initial_port=int(saved.get("mcp_port",8000))
        self.endpoint=QLineEdit(f"http://{initial_host}:{initial_port}/mcp"); self.endpoint.setReadOnly(True)
        copy_endpoint=QPushButton("Copy endpoint"); copy_endpoint.clicked.connect(lambda: QApplication.clipboard().setText(self.endpoint.text()))
        copy_config=QPushButton("Copy client config"); copy_config.clicked.connect(self.copy_mcp_client_config)
        endpoint_row=QHBoxLayout(); endpoint_row.addWidget(self.endpoint,1); endpoint_row.addWidget(copy_endpoint); endpoint_row.addWidget(copy_config)
        v.addWidget(self.server_status); v.addLayout(row); v.addWidget(QLabel("Streamable HTTP endpoint (for MCP clients — not a browser chat page)")); v.addLayout(endpoint_row)
        v.addWidget(QLabel('Opening /mcp directly in a browser may show "Missing session ID"; that is expected because a browser GET does not perform the MCP initialize handshake.'))
        v.addWidget(QLabel('Live GIMP mode: install the plug-in once, then restart GIMP. When connected, successful MCP edits are mirrored into the visible GIMP display.'))
        v.addStretch(); return w

    def copy_mcp_client_config(self):
        saved=CONTROL.load(); port=int(saved.get("mcp_port",8000))
        network=bool(saved.get("mcp_network_enabled",False))
        host=socket.gethostname() if network else "127.0.0.1"
        config={"name":"GIMP MCP Studio","url":f"http://{host}:{port}/mcp","transport":"streamable-http"}
        if network and saved.get("mcp_auth_token"):
            config["headers"]={"Authorization":f"Bearer {saved['mcp_auth_token']}"}
        QApplication.clipboard().setText(json.dumps(config,indent=2))
        self.statusBar().showMessage("MCP client configuration copied to clipboard.",5000)

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
            data={"gimp":self._setup_manager().managed_gimp_status(),"gimp_mcp":self._setup_manager().gimp_mcp_update_status(),"runtime":self._setup_manager().update_check()}; self.setup_output.setPlainText(json.dumps(data,indent=2,default=str))
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
        self.update_on_start=QCheckBox("Check GIMP and GIMP MCP updates on startup"); self.update_on_start.setChecked(bool(saved.get("check_updates_on_start",True)))
        self.network_enabled=QCheckBox("Allow external MCP clients on the network"); self.network_enabled.setChecked(bool(saved.get("mcp_network_enabled",False)))
        self.network_host=QLineEdit(str(saved.get("mcp_network_host","0.0.0.0")))
        self.mcp_port=QSpinBox(); self.mcp_port.setRange(1024,65535); self.mcp_port.setValue(int(saved.get("mcp_port",8000)))
        token=QLineEdit(str(saved.get("mcp_auth_token", ""))); token.setReadOnly(True); token.setEchoMode(QLineEdit.EchoMode.Password); self.mcp_token=token
        copy_token=QPushButton("Copy token"); copy_token.clicked.connect(lambda: QApplication.clipboard().setText(CONTROL.load().get("mcp_auth_token", "")))
        token_row=QHBoxLayout(); token_row.addWidget(token,1); token_row.addWidget(copy_token)
        token_wrap=QWidget(); token_wrap.setLayout(token_row)
        save=QPushButton("Save settings"); save.clicked.connect(self.save_settings)
        reconfigure=QPushButton("Reconfigure Setup"); reconfigure.clicked.connect(self.open_setup_wizard)
        actions=QHBoxLayout(); actions.addWidget(save); actions.addWidget(reconfigure); actions.addStretch(); action_wrap=QWidget(); action_wrap.setLayout(actions)
        form.addRow("Live preview interval (ms)", self.preview_interval)
        form.addRow("Preview max size", self.preview_size)
        form.addRow(self.update_on_start)
        form.addRow(self.network_enabled)
        form.addRow("Network bind address", self.network_host)
        form.addRow("MCP port", self.mcp_port)
        form.addRow("Network bearer token", token_wrap)
        form.addRow(QLabel("Local MCP always uses 127.0.0.1. Network access requires the bearer token and should be limited to trusted LAN/VPN networks."))
        form.addRow(action_wrap)
        return w

    def save_settings(self):
        import secrets
        data=CONTROL.load()
        network=self.network_enabled.isChecked()
        if network and not data.get("mcp_auth_token"):
            data["mcp_auth_token"]=secrets.token_urlsafe(36)
        data.update({
            "preview_interval_ms":self.preview_interval.value(),
            "preview_max":self.preview_size.value(),
            "check_updates_on_start":self.update_on_start.isChecked(),
            "mcp_network_enabled":network,
            "mcp_network_host":self.network_host.text().strip() or "0.0.0.0",
            "mcp_bind_host":(self.network_host.text().strip() or "0.0.0.0") if network else "127.0.0.1",
            "mcp_port":self.mcp_port.value(),
            "mcp_require_auth":True,
        })
        CONTROL.save(data); self.timer.setInterval(self.preview_interval.value()); self._apply_runtime_settings(data)
        self.mcp_token.setText(str(data.get("mcp_auth_token", "")))
        QMessageBox.information(self,"Settings saved","Settings were saved. Restart the MCP server to apply bind, port, authentication or GIMP runtime changes.")

    def save_ai_selection(self):
        data=CONTROL.load()
        data["ai_provider"]=self.provider.currentText()
        data["ai_model"]=self.model.currentData() or self.model.currentText()
        CONTROL.save(data)
        self.studio_provider.setCurrentText(data["ai_provider"])
        self.studio_refresh_models()
        self.provider_output.append(f"Selected: {data['ai_provider']} / {data['ai_model']}")

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
        saved=CONTROL.load(); self._apply_runtime_settings(saved)
        host=str(saved.get("mcp_bind_host","127.0.0.1")); port=int(saved.get("mcp_port",8000))
        env=os.environ.copy()
        if saved.get("mcp_network_enabled"):
            token=str(saved.get("mcp_auth_token") or "").strip()
            if not token:
                QMessageBox.critical(self,"MCP Server","Network MCP requires an authentication token. Save Settings or run Reconfigure Setup first."); return
            env["GIMP_MCP_AUTH_TOKEN"]=token
            env["GIMP_MCP_RESOURCE_URL"]=f"http://{host}:{port}/mcp"
        else:
            env.pop("GIMP_MCP_AUTH_TOKEN",None); env.pop("GIMP_MCP_RESOURCE_URL",None)
        code=f"from gimp_mcp.server import mcp; mcp.run(transport='streamable-http', host={host!r}, port={port})"
        cmd=[str(PROJECT_PYTHON),"-c",code]
        log=(STATE/"server.log").open("ab")
        p=subprocess.Popen(cmd,cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        PIDFILE.write_text(str(p.pid)); self.endpoint.setText(f"http://{host}:{port}/mcp"); self.refresh()

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
        proc.setProperty("provider", provider)
        proc.setArguments(["-m","gimp_mcp.provider_cli","status",provider])
        proc.finished.connect(lambda _code,_status,p=proc: self._provider_status_finished(p))
        self._provider_process=proc
        proc.start()

    def _provider_status_finished(self, proc):
        provider=str(proc.property("provider") or "")
        stdout=bytes(proc.readAllStandardOutput()).decode("utf-8","replace")
        stderr=bytes(proc.readAllStandardError()).decode("utf-8","replace")
        if provider != self.provider.currentText():
            proc.deleteLater(); return
        text=(stdout or stderr).strip(); self.provider_output.setPlainText(text); self.model.clear()
        try:
            payload=json.loads(stdout); models=payload.get("models") or []
            for item in models:
                if isinstance(item,dict):
                    mid=str(item.get("id") or item.get("model") or item.get("name") or ""); label=str(item.get("display") or item.get("display_name") or item.get("name") or mid); self.model.addItem(label,mid)
                else: self.model.addItem(str(item),str(item))
            wanted=CONTROL.load().get("ai_model","")
            if not self._model_matches_provider(provider, wanted): wanted=""
            selected=False
            for i in range(self.model.count()):
                if self.model.itemData(i)==wanted:
                    self.model.setCurrentIndex(i); selected=True; break
            if not selected and self.model.count(): self.model.setCurrentIndex(0)
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
        sid=str(sid or "").strip()
        self._preview_session_id=sid or None
        self._preview_signature=None
        self._load_preview(sid, force=True)

    def _apply_preview_pixmap(self):
        if not self._preview_pixmap or self._preview_pixmap.isNull():
            return
        size=self.preview.contentsRect().size()
        if size.width() < 2 or size.height() < 2:
            return
        pix=self._preview_pixmap.scaled(size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)

    def _load_preview(self, sid, force=False):
        sid=str(sid or "").strip()
        if not sid:
            self._preview_pixmap=None; self._preview_signature=None
            self.preview.clear(); self.preview.setText("No session selected"); return
        path=SESSIONS/sid/"preview.png"
        if not path.is_file():
            self._preview_pixmap=None; self._preview_signature=None
            self.preview.clear(); self.preview.setText("No preview rendered yet for this session"); return
        try:
            stat=path.stat(); signature=(stat.st_mtime_ns,stat.st_size)
            if not force and signature == self._preview_signature and self._preview_pixmap:
                return
            data=path.read_bytes()
            image=QImage()
            if not image.loadFromData(data):
                raise ValueError(f"invalid/incomplete preview image ({len(data)} bytes)")
            self._preview_pixmap=QPixmap.fromImage(image)
            self._preview_signature=signature
            self._preview_session_id=sid
            self.preview.clear(); self._apply_preview_pixmap()
            updated=time.strftime("%H:%M:%S", time.localtime(stat.st_mtime))
            self.preview_meta.setText(f"Session: {sid[:8]}… · Preview: {image.width()}×{image.height()} · Updated: {updated} · Size: {len(data) // 1024 or 1} KB")
            self.preview.setToolTip(f"{path} · {image.width()}×{image.height()} · {len(data)} bytes")
        except Exception as exc:
            # Keep the previous complete frame on transient read errors instead of blanking it.
            if self._preview_pixmap:
                self._apply_preview_pixmap()
                self.preview.setToolTip(f"Preview refresh delayed: {exc}")
            else:
                self.preview.clear(); self.preview.setText(f"Preview load failed: {exc}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_preview_pixmap", None):
            self._apply_preview_pixmap()

    def _selected_session_id(self):
        active_sid=None
        if self._studio_job_id:
            try:
                from gimp_mcp.server import jobs
                active_sid=jobs.get(self._studio_job_id).session_id
            except Exception:
                pass
        item=self.sessions.currentItem() if hasattr(self,"sessions") else None
        selected=item.text().strip() if item and item.text().strip() else None
        return _prefer_active_session(active_sid, selected)

    def export_selected_artwork(self, fmt):
        sid=self._selected_session_id()
        if not sid:
            QMessageBox.warning(self,"Export artwork","No artwork session selected."); return
        ext="jpg" if fmt=="jpg" else fmt
        filters={"xcf":"GIMP Project (*.xcf)","png":"PNG Image (*.png)","jpg":"JPEG Image (*.jpg *.jpeg)"}
        default=Path.home()/"Pictures"/f"gimp-mcp-{sid[:8]}.{ext}"
        target,_=QFileDialog.getSaveFileName(self,f"Export {fmt.upper()}",str(default),filters[fmt])
        if not target: return
        try:
            from gimp_mcp.server import exports, sessions
            session=sessions.get(sid)
            result=exports.export(session,target,fmt,overwrite=True)
            GUI_LOGGER.info("Export complete sid=%s format=%s output=%s", sid, fmt, result.get("output"))
            QMessageBox.information(self,"Export complete",f"Saved {fmt.upper()} to:\n{result['output']}")
        except Exception as exc:
            GUI_LOGGER.exception("Export failed sid=%s format=%s", sid, fmt)
            QMessageBox.critical(self,"Export failed",str(exc))

    def refresh(self):
        pid=self._server_pid(); self.server_status.setText(f"Server: {'RUNNING pid='+str(pid) if pid else 'STOPPED'}")
        live=LiveBridge(timeout=0.25).status()
        if live.get('stale_socket_removed'):
            GUI_LOGGER.info("Removed stale GIMP live socket %s", live.get('socket'))
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
        if getattr(self, "follow_active_session", None) and self.follow_active_session.isChecked() and self._studio_job_id:
            try:
                from gimp_mcp.server import jobs
                active_sid=jobs.get(self._studio_job_id).session_id
                if active_sid in ids:
                    wanted=ids.index(active_sid)
                    if self.sessions.currentRow() != wanted:
                        self.sessions.setCurrentRow(wanted)
            except Exception:
                pass
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

    def closeEvent(self, event):
        GUI_LOGGER.info("Control Center closing pid=%s", os.getpid())
        # QProcess children can otherwise finish during Qt teardown and call
        # slots on already-destroyed wrappers. Stop only helper processes we own.
        for attr in ("_studio_models_process", "_provider_process", "_startup_update_process"):
            proc=getattr(self,attr,None)
            if proc is None:
                continue
            try:
                proc.finished.disconnect()
            except Exception:
                pass
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.terminate()
                    if not proc.waitForFinished(1000):
                        proc.kill(); proc.waitForFinished(1000)
            except RuntimeError:
                pass
            setattr(self,attr,None)
        super().closeEvent(event)


def main():
    def _excepthook(exc_type, exc, tb):
        GUI_LOGGER.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook=_excepthook
    app=QApplication(sys.argv); win=ControlCenter(); win.show(); return app.exec()

if __name__ == "__main__": raise SystemExit(main())
