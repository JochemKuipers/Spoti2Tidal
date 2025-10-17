from __future__ import annotations
from typing import Tuple

from PyQt6 import uic
from PyQt6.QtWidgets import QDialog, QWidget

from src.services.tidal import Tidal


class LoginTidalDialog(QDialog):
    def __init__(self, parent: QWidget | None, tidal: Tidal) -> None:
        super().__init__(parent)
        uic.loadUi("src/gui/ui/login_tidal_dialog.ui", self)
        self._tidal = tidal

        # Wire
        self.openBrowserButton.clicked.connect(self._open_browser)
        self.completeButton.clicked.connect(self._complete)
        self.cancelButton.clicked.connect(self.reject)

    def _open_browser(self) -> None:
        self._tidal.open_browser_login()

    def _complete(self) -> None:
        url = self.redirectUrlEdit.text().strip()
        if not url:
            self.reject()
            return
        ok = self._tidal.complete_pkce_login(url)
        if ok:
            self.accept()
        else:
            self.reject()


class ConfirmTransferDialog(QDialog):
    def __init__(self, parent: QWidget | None, default_name: str = "") -> None:
        super().__init__(parent)
        uic.loadUi("src/gui/ui/confirm_transfer_dialog.ui", self)
        if default_name:
            self.playlistNameEdit.setText(default_name)

        self.confirmButton.clicked.connect(self.accept)
        self.cancelButton.clicked.connect(self.reject)

    def get_values(self) -> Tuple[str, str, bool]:
        return (
            self.playlistNameEdit.text().strip(),
            self.playlistDescEdit.text().strip(),
            bool(self.skipExistingCheckbox.isChecked()),
        )


