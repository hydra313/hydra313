# ConsoleHawk Windows executable

Double-click **ConsoleHawk.exe** to start the installed app in its own window.
The executable has the ConsoleHawk hawk icon and Windows product metadata. It
starts the existing hidden server if needed, waits for readiness, and opens the
dashboard without a console window. Simultaneous launch clicks are serialized.

This is a native Windows GUI launcher for the existing Python installation, not
a bundled standalone Python runtime. Keep the source installation and its `.venv`
folder. No command typing is needed for daily use. The server and background
collector keep running after the dashboard closes.

For a source checkout, complete the installation in the root README, then open
`desktop/ConsoleHawk.exe`. It discovers the parent installation automatically.
For a separate launch folder, run once from the repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Build-Launcher.ps1 -Destination C:\ConsoleHawk
```

The build uses the Windows .NET Framework compiler and the committed icon. It
creates `ConsoleHawk.exe`, `App/project-path.txt`, and `Assets/consolehawk.ico`.
The path file points to the existing installation; it is not a credentials file.
Keep the launch folder together. Rebuild if you move the application installation.
The executable supports `--check` for a silent installation check (0=valid, 2=missing).

The local organized launch folder contains:

- ConsoleHawk.exe — the main launch file.
- App — installation location used by the launcher.
- Assets — logo files.
- Guides — setup, feature and validation notes.
- Screenshots — saved UI examples.
- Releases — source ZIP and complete patch.
- Archive — older helper files and launchers, retained for reference.

Old archived launchers are historical and may refer to their former locations.
Use the top-level executable. Existing SQLite, protected Discord settings and
browser profiles remain at their original installation location and are never
included in the release ZIP.

Verification: six launcher tests passed, including compilation, PE executable
format, embedded icon, Windows product name, paths with spaces, missing-installation
handling, and source-checkout discovery. The installed EXE launched with exit 0.
Background source sign-in/verification is separate from launching the application.
If a site pauses, open Connections, finish sign-in, and click Resume scanning.
