# Building the whiteboard

## Windows

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment:
   `python -m venv .venv`
   `.venv\Scripts\python.exe -m pip install -r requirements.txt`
3. Build both executables:
   `build\build_all.bat`
4. Results are in `dist\` and the clean release bundle is in `release\`.

Server only:
`build\build_server.bat`

Client only:
`build\build_client.bat`

## Linux (Ubuntu)

1. Install system packages:
   `sudo apt install python3 python3-venv python3-tk`
2. Create and activate a virtual environment:
   `python3 -m venv .venv`
   `.venv/bin/python -m pip install -r requirements.txt`
3. Build both executables:
   `bash build/build_all.sh`
4. Results are in `dist/` and the release bundle is in `release/`.

## If the build fails

- Missing Tcl or Tk files: install `python3-tk` on Linux, or reinstall Python on Windows with Tcl/Tk support. PyInstaller needs the Tcl/Tk data files that ship with tkinter.
- Missing Pillow: install it with `pip install Pillow`; export falls back to PostScript without it, but the client build is cleaner with Pillow installed.
- Missing module errors: add the module to the `--hidden-import` list in the build script.

## Known limitation

Each operating system needs its own build. A Windows executable will not run on Linux, and a Linux binary will not run on Windows. Build on the target platform.