# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a project of a production app to enable OCR for gas cylinders in Maxima factory.
This program is installed on an airgapped computers with multiple USB cameras attached.
At this point, each pc is responsible on a single workstation and all cameras are related to the same cylinder.

This project should contain all the necessary tools to compile and install OCR recognition app on factory computers.

The structure of the project is as follows:
modbus_server.py is the modbus server and currently the only public API for factory integration. It is debugged and
working and not expected to be changed beside delegating work to other modules.

## Architecture

### Runtime
This is a single-process Python service that exposes a **Modbus TCP slave** so a PLC/master can trigger cameras 
recognition. It is expected to have multiple cameras attached, capturing different angles of the cylinder.

The full request/response protocol is documented in `README.md` (button-style trigger on holding register 1, 
status on register 2, ASCII null-terminated result starting at register 3).

The OCR job should be delegated to a wheel library located at
https://github.com/dennispo/maxima-ocr-learning/releases/tag/wheel-vx.y.z and the models bundle located at
https://github.com/dennispo/maxima-ocr-learning/releases/tag/bundle-YYYYMMDD. Integration documentation is located
inside the wheel.

### Deployment
The deployment is an installer on an airgapped PC inside a factory. The installer should be a windows executable
that installs all the necessary files to C:/Maxima/OCR folder and registers the program as a windows service. It should
also contain scripts to start, stop and restart the service.

Version upgrade will be performed by manually downloading the installer and executing it on the factory PC.

## Development Best Practices
During the design and planning phases, ask question if necessary, but don't ask questions if you can get the answer by
exploring the code. Apply critical thinking during design to make sure the outcome aligns with project priorities.
Stick to domain driven design principles.

When creating execution plan, always include dependencies between tasks. When executing, execute sub-agents in parallel
if possible.

Invoke TDD approach for implementation phase. Always create tests based on the spec. Consider tests as a list of
requirements. If test is failed, make sure the test is still representing the specs and change the code - not the test.
If you find discrepancy between test and spec, always ask the user before changing the test.

At the end of the task, always make sure all tests passes and update the user documentation.

## Common commands

Install dependencies:
```
pip install -r requirements.txt
```

Run the server locally (must be invoked from the repo root — `config.py` reads `./config.yaml` from `os.getcwd()`):
```
python modbus_server.py
```

Build the Windows distributable (uses PyInstaller; produces `dist/modbus_server.exe` and copies `config.yaml` next to it):
```
compile.bat
```


### Things to know when changing this code

- The result protocol is "ASCII bytes, one per holding register, terminated by a `0` register." If you change what `handle_background_task` writes to `RESULT_ADDRESS`, preserve the trailing `0` — masters rely on it to find the end of the string.
- `CallbackDataBlock.setValues` must return a pymodbus exception code (`ExcCodes.*`) or the parent's return value; returning `None` from the trigger path will surface as a Modbus error to the client.
- Camera enumeration and OpenCV backends are platform-specific. `config.yaml` ships with `DSHOW` (Windows DirectShow) because the release target is Windows; on macOS/Linux you'll typically need `ANY`, `AVFOUNDATION` (not currently mapped), or `V4L2`.
- The shipped artifact is a single PyInstaller `.exe` with `config.yaml` next to it — keep `config.yaml` loadable from the working directory and avoid adding imports that don't bundle cleanly with PyInstaller.
