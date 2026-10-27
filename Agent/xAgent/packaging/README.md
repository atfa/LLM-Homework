# xAgent Windows Installer

Windows 10/11 x64, Python 3.12 for building only. Students do not need Python.

1. Install build deps: `python -m pip install -e . pyinstaller pillow pytest`.
2. Generate icon: `python packaging/make_icon.py`.
3. Run the PyInstaller command from `.github/workflows/xagent-windows.yml`.
4. Build the installer with Inno Setup 6: `ISCC packaging\\xagent.iss`.
5. Download `dist-installer/xAgent-Setup-0.1.0.exe`.

Alternatively, run the GitHub Actions workflow manually. A tag `xagent-v0.1.0` publishes a release.

At first launch the packaged app creates `%APPDATA%\\xAgent\\config.ini` and opens Notepad for students to set base_url, model and api_key. Relaunch the app after saving.

The application starts in `Documents\\xAgent\\workspace` when launched from installed shortcuts. CLI calls execute with the current user's permissions. Only run trusted instructions; this course version has no command-approval prompt.

Windows SmartScreen may warn for unsigned EXE files. Test installer on a clean Windows VM before distributing.
