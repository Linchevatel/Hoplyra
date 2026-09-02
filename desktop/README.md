# Hoplyra Desktop

Portable desktop builds — local control plane with embedded dashboard.

## Release files

```
GitHub Releases (v1.4.2):
Hoplyra-1.4.2-x86_64.AppImage
Hoplyra-1.4.2-x64-portable.exe
SHA256SUMS
README.txt
```

## Build Linux AppImage

Requirements on the **build machine**: Node.js 20+, Python 3.11+, Docker (recommended).

```bash
cd desktop
npm install
npm run build
```

## Build Windows portable

GitHub Actions in `.github/workflows/release-desktop.yml`, or locally on Windows:

```powershell
cd desktop
npm install
npm run build:win
```

## Run on Linux (x86_64)

Requires **glibc 2.35+** (Ubuntu 22.04+, Debian 12+, Fedora 36+).

```bash
sudo apt install libfuse2   # Ubuntu/Debian
chmod +x Hoplyra-1.4.2-x86_64.AppImage
./Hoplyra-1.4.2-x86_64.AppImage
```

If AppImage does not start (missing FUSE):

```bash
./Hoplyra-1.4.2-x86_64.AppImage --appimage-extract-and-run
```

## Run on Windows

Double-click `Hoplyra-1.4.2-x64-portable.exe`.

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
