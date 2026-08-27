# Hoplyra Desktop

Portable desktop builds — local control plane with embedded dashboard.

## Release files

```
GitHub Releases (v1.4.0):
Hoplyra-1.4.0-x86_64.AppImage
Hoplyra-1.4.0-x64-portable.exe
GitHub Releases (v1.4.1):
- `Hoplyra-1.4.1-x86_64.AppImage` (Linux x86_64)
- `Hoplyra-1.4.1-x64-portable.exe` (Windows x64 portable)

Also committed under Git LFS in this repository:
- `desktop/dist/Hoplyra-1.4.1-x86_64.AppImage`
- `desktop/dist/Hoplyra-1.4.1-x64-portable.exe`

---

## 2. Requirements

### Linux
- **64-bit x86_64**
- **glibc 2.35+** (Ubuntu 22.04+, Debian 12+, Fedora 36+, Arch Linux)
- `libfuse2` (if AppImage fails to mount on Ubuntu 22.04+):
  ```bash
  sudo apt install libfuse2
  ```

### Windows
- **64-bit Windows 10 / 11**
- No additional runtime required.

---

## 3. Running

### Linux

```bash
chmod +x Hoplyra-1.4.1-x86_64.AppImage
./Hoplyra-1.4.1-x86_64.AppImage
```

If FUSE is not installed on the system:

```bash
./Hoplyra-1.4.1-x86_64.AppImage --appimage-extract-and-run
```

### Windows

Double-click `Hoplyra-1.4.1-x64-portable.exe`.


Data directory: `%APPDATA%\hoplyra-desktop\hoplyra-data\`

## Automated Releases

Releases are generated automatically via GitHub Actions workflow (`.github/workflows/release-desktop.yml`) on git tags (`v*`).

## Dev (from source)

```bash
npm start
```

Uses local Python venv in `../backend`, not the bundled binary.

## Architecture

```
AppImage → Electron UI → hoplyra-backend (PyInstaller) → FastAPI + SQLite
```

User data: `~/.config/hoplyra-desktop/hoplyra-data/`

Default login: `admin` / `admin`
