from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QTextEdit, QVBoxLayout,
    QWizard, QWizardPage,
)

from .control_settings import ControlSettings
from .setup_manager import SetupManager


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)


class SetupWizard(QWizard):
    """First-run and reconfiguration wizard for the desktop product."""

    def __init__(self, control: ControlSettings, root: Path, parent=None) -> None:
        super().__init__(parent)
        self.control = control
        self.root = root
        self.manager = SetupManager(root)
        self.saved = control.load()
        self.setWindowTitle("GIMP MCP Studio Setup")
        self.setMinimumSize(760, 560)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self._signals = _Signals()
        self._signals.done.connect(self._gimp_install_done)
        self._signals.failed.connect(self._gimp_install_failed)
        self.addPage(self._welcome_page())
        self.addPage(self._gimp_page())
        self.addPage(self._ai_page())
        self.addPage(self._mcp_page())
        self.addPage(self._finish_page())
        self._signals.progress.connect(self.gimp_progress.setValue)
        self.accepted.connect(self._save)

    def _welcome_page(self) -> QWizardPage:
        page=QWizardPage(); page.setTitle("Welcome to GIMP MCP Studio")
        v=QVBoxLayout(page)
        text=QLabel(
            "This setup connects an AI to a running GIMP through MCP. GIMP remains the live workspace, "
            "so you can watch changes and edit the same document yourself at any time."
        )
        text.setWordWrap(True); v.addWidget(text)
        v.addWidget(QLabel("Setup covers: GIMP runtime, live plug-in, AI providers, local MCP and optional secure network access."))
        v.addStretch(); return page

    def _gimp_page(self) -> QWizardPage:
        page=QWizardPage(); page.setTitle("GIMP Runtime"); self._gimp_page_obj=page
        v=QVBoxLayout(page)
        self.gimp_status=QTextEdit(); self.gimp_status.setReadOnly(True); self.gimp_status.setMaximumHeight(150)
        self.use_system=QRadioButton("Use the system GIMP installation")
        self.use_managed=QRadioButton("Use the latest official GIMP AppImage managed by GIMP MCP Studio")
        (self.use_managed if self.saved.get("gimp_runtime") == "managed" else self.use_system).setChecked(True)
        self.gimp_install=QPushButton("Download / Update Official GIMP")
        self.gimp_install.clicked.connect(self._install_gimp)
        self.gimp_progress=QProgressBar(); self.gimp_progress.setRange(0,100); self.gimp_progress.setValue(0)
        refresh=QPushButton("Refresh GIMP Status"); refresh.clicked.connect(self.refresh_gimp_status)
        v.addWidget(self.use_system); v.addWidget(self.use_managed)
        row=QHBoxLayout(); row.addWidget(self.gimp_install); row.addWidget(refresh); row.addStretch(); v.addLayout(row)
        v.addWidget(self.gimp_progress); v.addWidget(self.gimp_status)
        note=QLabel("Managed GIMP is downloaded only from the official GIMP download server and verified against its published SHA256 checksum. No root access is required.")
        note.setWordWrap(True); v.addWidget(note); v.addStretch()
        self.refresh_gimp_status(); return page

    def refresh_gimp_status(self) -> None:
        try:
            data=self.manager.managed_gimp_status()
            self.gimp_status.setPlainText(
                f"System GIMP: {data.get('system_version') or 'not found'}\n"
                f"Managed GIMP: {data.get('managed_version') or 'not installed'}\n"
                f"Latest official stable: {data.get('latest_version') or 'unknown'}\n"
                f"Managed path: {data.get('managed_path')}"
            )
            if not self.saved.get("first_run_complete") and data.get("update_available") and not data.get("managed_version"):
                self.use_managed.setChecked(True)
        except Exception as exc:
            self.gimp_status.setPlainText(f"Could not check the official GIMP release: {exc}")

    def validateCurrentPage(self) -> bool:
        if self.currentPage() is getattr(self, "_gimp_page_obj", None) and self.use_managed.isChecked():
            try:
                status=self.manager.managed_gimp_status()
                if status.get("managed_version") != status.get("latest_version"):
                    if self.gimp_install.isEnabled():
                        self.gimp_status.append("\nDownloading and verifying the latest official GIMP before continuing...")
                        self._install_gimp()
                    return False
            except Exception as exc:
                QMessageBox.critical(self,"GIMP check failed",str(exc))
                return False
        return super().validateCurrentPage()

    def _install_gimp(self) -> None:
        self.gimp_install.setEnabled(False); self.gimp_progress.setValue(0)
        def work():
            try:
                def progress(done,total):
                    self._signals.progress.emit(int(done*100/total) if total else 0)
                self._signals.done.emit(self.manager.install_latest_gimp_appimage(progress))
            except Exception as exc:
                self._signals.failed.emit(str(exc))
        threading.Thread(target=work, daemon=True).start()

    def _gimp_install_done(self, result: object) -> None:
        self.gimp_install.setEnabled(True); self.gimp_progress.setValue(100); self.use_managed.setChecked(True)
        if isinstance(result, dict):
            self.gimp_status.append(f"\nInstalled GIMP {result.get('version')} successfully.")
        self.refresh_gimp_status()

    def _gimp_install_failed(self, error: str) -> None:
        self.gimp_install.setEnabled(True)
        QMessageBox.critical(self, "GIMP installation failed", error)

    def _ai_page(self) -> QWizardPage:
        page=QWizardPage(); page.setTitle("AI Providers")
        v=QVBoxLayout(page)
        text=QLabel("Link any providers you want to use inside GIMP MCP Studio. Provider credentials remain owned by their official login tools.")
        text.setWordWrap(True); v.addWidget(text)
        self.ai_status=QTextEdit(); self.ai_status.setReadOnly(True); self.ai_status.setMaximumHeight(170)
        for provider,label in (("chatgpt","ChatGPT / OpenAI"),("claude","Claude"),("gemini","Google Gemini / Antigravity"),("mistral","Mistral")):
            row=QHBoxLayout(); row.addWidget(QLabel(label)); row.addStretch()
            b=QPushButton("Link / Sign in"); b.clicked.connect(lambda _=False,p=provider:self._link_provider(p)); row.addWidget(b); v.addLayout(row)
        refresh=QPushButton("Refresh Provider Status"); refresh.clicked.connect(self._refresh_provider_status); v.addWidget(refresh)
        v.addWidget(self.ai_status)
        info=QLabel("AILinux / TriForce can also be selected in the app. External AI clients do not need a built-in provider login; they can connect through MCP on the next page.")
        info.setWordWrap(True); v.addWidget(info); v.addStretch(); self._refresh_provider_status(); return page

    def _link_provider(self, provider: str) -> None:
        py=self.root/".venv/bin/python"
        if not py.exists():
            QMessageBox.warning(self,"Provider login","Project runtime is missing. Run the project setup first."); return
        subprocess.Popen([str(py),"-m","gimp_mcp.provider_cli","connect",provider],cwd=str(self.root),start_new_session=True)
        self.ai_status.append(f"Started {provider} login. Complete the provider flow, then refresh status.")

    def _refresh_provider_status(self) -> None:
        py=self.root/".venv/bin/python"
        if not py.exists(): self.ai_status.setPlainText("Project runtime not available."); return
        rows=[]
        for provider in ("chatgpt","claude","gemini","mistral"):
            try:
                r=subprocess.run([str(py),"-m","gimp_mcp.provider_cli","status",provider],cwd=str(self.root),text=True,capture_output=True,timeout=12)
                payload=json.loads(r.stdout or "{}")
                st=payload.get("status") or {}; rows.append(f"{provider}: {'connected' if st.get('authenticated') else 'not connected'}")
            except Exception as exc: rows.append(f"{provider}: check failed ({exc})")
        self.ai_status.setPlainText("\n".join(rows))

    def _mcp_page(self) -> QWizardPage:
        page=QWizardPage(); page.setTitle("MCP Connection")
        form=QFormLayout(page)
        self.network=QCheckBox("Allow other computers / AI clients to connect over the network")
        self.network.setChecked(bool(self.saved.get("mcp_network_enabled",False)))
        self.bind=QLineEdit(str(self.saved.get("mcp_network_host","0.0.0.0")))
        self.port=QSpinBox(); self.port.setRange(1024,65535); self.port.setValue(int(self.saved.get("mcp_port",8000)))
        self.require_auth=QCheckBox("Require bearer-token authentication for network MCP")
        self.require_auth.setChecked(True); self.require_auth.setEnabled(False)
        self.check_updates=QCheckBox("Check GIMP and GIMP MCP updates when the app starts")
        self.check_updates.setChecked(bool(self.saved.get("check_updates_on_start",True)))
        form.addRow("Local endpoint", QLabel(f"http://127.0.0.1:{self.port.value()}/mcp"))
        form.addRow(self.network); form.addRow("Network bind address",self.bind); form.addRow(self.require_auth); form.addRow(self.check_updates)
        warning=QLabel("Network mode is disabled by default. When enabled, GIMP MCP Studio generates and requires a private bearer token. Do not expose the endpoint directly to the public Internet.")
        warning.setWordWrap(True); form.addRow(warning); return page

    def _finish_page(self) -> QWizardPage:
        page=QWizardPage(); page.setTitle("Ready")
        v=QVBoxLayout(page)
        t=QLabel("GIMP MCP Studio will use GIMP as the live workspace. You can re-run this setup at any time from Settings → Reconfigure Setup.")
        t.setWordWrap(True); v.addWidget(t); v.addStretch(); return page

    def _save(self) -> None:
        import secrets
        data=self.control.load()
        data.update({
            "first_run_complete": True,
            "gimp_runtime": "managed" if self.use_managed.isChecked() else "system",
            "managed_gimp_path": str(Path.home()/".local/share/gimp-mcp/runtime/gimp.AppImage") if self.use_managed.isChecked() else "",
            "check_updates_on_start": self.check_updates.isChecked(),
            "mcp_network_enabled": self.network.isChecked(),
            "mcp_network_host": self.bind.text().strip() or "0.0.0.0",
            "mcp_bind_host": (self.bind.text().strip() or "0.0.0.0") if self.network.isChecked() else "127.0.0.1",
            "mcp_port": self.port.value(),
            "mcp_require_auth": True,
        })
        if self.network.isChecked() and not data.get("mcp_auth_token"):
            data["mcp_auth_token"]=secrets.token_urlsafe(36)
        self.control.save(data)
