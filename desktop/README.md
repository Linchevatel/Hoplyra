# Hoplyra Desktop

Portable Linux AppImage — local control plane with embedded dashboard.

## Build release AppImage

Requirements on the **build machine** only: Node.js 20+, Python 3.11+, `npm`.

```bash
cd desktop
npm install
npm run build
```

Output (single file, ready for git or upload):

```
desktop/dist/Hoplyra-1.3.1-x86_64.AppImage
desktop/dist/SHA256SUMS
desktop/dist/README.txt
```

The script removes intermediate artifacts (`release/`, `resources/`, `linux-unpacked/`).

## Run on any Linux (x86_64)

Requires **glibc 2.35+** (Ubuntu 22.04+, Debian 12+, Fedora 36+).

```bash
sudo apt install libfuse2   # Ubuntu/Debian
chmod +x Hoplyra-1.3.1-x86_64.AppImage
./Hoplyra-1.3.1-x86_64.AppImage
```

If AppImage does not start (missing FUSE):

```bash
./Hoplyra-1.3.1-x86_64.AppImage --appimage-extract-and-run
```

On Alt / Debian / Ubuntu install FUSE if needed:

```bash
sudo apt install fuse libfuse2   # Debian/Ubuntu/Alt
sudo dnf install fuse fuse-libs  # Fedora
```

## Commit to git

Track only the release folder contents:

```
dist/Hoplyra-*-x86_64.AppImage
dist/SHA256SUMS
dist/README.txt
build/icons/
```

Everything else is in `.gitignore`.

**Size ~130–140 MB.** GitHub rejects files **> 100 MB** — use one of:

1. **GitHub Releases** (recommended) — attach AppImage to a release tag
2. **Git LFS** — `.gitattributes` already prepared:
   ```bash
   git lfs install
   git lfs track "*.AppImage"
   ```

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
