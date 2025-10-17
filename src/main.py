from __future__ import annotations
import sys
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow
from logging_config import setup_logging


def main():
    setup_logging()
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
