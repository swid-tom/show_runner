# Show_Runner

<img src="/Images/item-1.png" alt="Image Description" width="300" height="300">

### (Streamlit Version)
<img src="/Images/img_1_1.png" alt="Image Description" width="500" height="300">

### (TKinter Version)
<img src="/Images/img_1_2.png" alt="Image Description" width="500" height="300">

----------------------------------------------------------

# Show_Runner

ShowRunner is a Windows desktop app made in python for network engineers to run any show command across multiple devices, collect all output, filter results, and export to CSV.
It supports structured parsing via TextFSM (using bundled ntc-templates), so you get clean tables for most Cisco/Arista/Juniper commands.

Was a side project to test out vibe coding - still a bit of cleanup to do.

Have TKinter version and Streamlit version in a already set executabale in the folders in the repo, plus the code.

**Side note ~ Can run any command really, no restriction.

----------------------------------------------------------

## Features

- Run any show command (e.g., show ip int br, show version, etc.) on a list of hosts.
- Parallel SSH via Netmiko for fast collection.
- Post-collection filtering (host, output text, regex).
- Structured parsing via TextFSM (ntc-templates included).
- Export: filtered line view, all lines, or structured CSV.
- No Python required on the target PC—just run the EXE.

----------------------------------------------------------

## Quick Start


### 1.Prepare your hosts file

A plain text file, one IP or hostname per line:
```
10.1.1.1
10.1.1.2
router1.example.com
```

### 2. Run the EXE

Double-click ShowRunner.exe (or run from CMD for logs):
```
ShowRunner.exe
```
- The app will open a browser window at http://127.0.0.1:8501 or next available port.
- If it doesn’t, copy the URL printed in the console and paste it into your browser.

### 3. Use the app

- Enter your SSH credentials, device type (e.g., cisco_ios), and the show command.
- Upload your hosts file.
- Click Run collection.
- Filter results as needed.
- Download CSVs (structured, filtered, or raw).

----------------------------------------------------------

## Building the EXE (for developers)

### Prerequisites

- Windows 10/11
- Python 3.8+ (recommended: use a virtualenv)
- PyInstaller
- Streamlit
- Netmiko
- ntc-templates
- TextFSM
- Paramiko
- Pandas

### Install dependencies
```
python -m venv .venv
.\.venv\Scripts\activate
pip install streamlit netmiko textfsm ntc-templates paramiko pandas pyinstaller
```

### Build the EXE
Use this command in CMD.exe (not PowerShell):
```
pyinstaller ^
  --onefile ^
  --console ^
  --name ShowRunner ^
  --add-data "app.py;." ^
  --collect-all streamlit ^
  --collect-all netmiko ^
  --collect-all paramiko ^
  --collect-all ntc_templates ^
  --collect-all cryptography ^
  --copy-metadata streamlit ^
  --hidden-import streamlit.runtime.scriptrunner.magic_funcs ^
  --hidden-import cryptography ^
  --hidden-import cryptography.hazmat.bindings._rust ^
  --hidden-import bcrypt ^
  --hidden-import pynacl ^
  --hidden-import nacl ^
  launch.py
```
### Notes:
- app.py is your Streamlit app (must be in the same folder as launch.py).
- launch.py is the launcher script that sets up environment variables and starts Streamlit.
- ntc_templates is bundled for offline TextFSM parsing.
- --console is recommended for debugging; switch to --noconsole for silent runs.
- You can add --icon "showrunner.ico" for branding.

### Output

- The EXE will be in dist\ShowRunner.exe.
- All required data (templates, etc.) is bundled.

----------------------------------------------------------

## Troubleshooting
- Blank console, instant close:
  Run from CMD to see error messages. Check %TEMP%\CellularCollector.log for details.
- TextFSM structured parsing not working:
  Make sure ntc_templates is bundled and NET_TEXTFSM is set by the launcher.
- "server.port does not work when global.developmentMode is true":
  The launcher disables Streamlit dev mode before starting; if you modify it, keep this fix.
- Firewall blocks browser auto-open:
  Copy the printed URL and paste it into your browser manually.

----------------------------------------------------------

## Packaging Tips
- Use --onedir during development for easier debugging.
- Switch to --onefile for distribution.
- Use a .spec file for repeatable builds.
- Sign your EXE with your org’s code signing certificate for best user experience.

----------------------------------------------------------

## Customization
- Multi-command support:
  Extend app.py to accept multiple commands and tag results with a command column.
- Branding:
  Add your own icon and splash screen.
- Silent mode:
  Use --noconsole once stable.

----------------------------------------------------------

## Credits
- Streamlit
- Netmiko
- ntc-templates
- TextFSM
- Paramiko
- Pandas

----------------------------------------------------------

# What is the "TextFSM templates ZIP" option?
TextFSM is a parsing engine that uses templates to turn raw CLI output (like show ip int br) into structured tables.
Your app can use the community ntc-templates (bundled by default), but you can also upload your own custom templates ZIP for:

- Vendor-specific commands
- Custom parsing needs
- New commands not covered by ntc-templates

----------------------------------------------------------

## What should the ZIP contain?

Your ZIP file should include:
1. Index file
    - This is a plain text file listing all available templates and mapping them to platform/command pairs.
    - Example line:
```
    cisco_ios_show_ip_interface_brief.template, cisco_ios, show ip interface brief
```

2. .template files
    - Each template is a .template file describing how to parse a specific command’s output.
    - https://github.com/networktocode/ntc-templates/tree/master/ntc_templates/templates
    - Example:
```
Value INTERFACE (\S+)
Value IPADDR (\S+)
...
Start
  ^${INTERFACE}\s+${IPADDR} ...
```

3. Folder structure
- The ZIP can have all files at the root, or inside a templates/ folder.
- The app will search for a folder containing an index file.

### Example ZIP contents:
```
templates/
  index
  cisco_ios_show_ip_interface_brief.template
  cisco_ios_show_version.template
  arista_eos_show_interfaces_status.template
```
Or just:
```
index
cisco_ios_show_ip_interface_brief.template
cisco_ios_show_version.template
```
----------------------------------------------------------

## How does the app use your ZIP?
- When you upload the ZIP, the app extracts it to a temporary folder.
- It sets the environment variable NET_TEXTFSM to point to the extracted templates directory.
- When you run a command, the app tries to find a matching template in your uploaded set.
- If a template matches, the output is parsed into a structured table (columns with field names).
- If no template matches, the app falls back to raw line view or regex/split parsing.

----------------------------------------------------------

## Why use custom templates?
- Extend parsing to commands not covered by ntc-templates.
- Fix parsing bugs for your environment.
- Support non-Cisco platforms (e.g., Juniper, Arista, Huawei).
- Customize field names or output formats.

----------------------------------------------------------

## How to create your own templates and index
- See ntc-templates documentation for template syntax and examples.
- The index file maps each template to a platform and command.
- Place your templates and index in a folder, zip it, and upload via the app.

----------------------------------------------------------

### In short:
Uploading a TextFSM templates ZIP lets you use your own parsing rules for structured output, making the app much more flexible for custom environments and commands.


----------------------------------------------------------
### Notes on Tkinter version:

Created a Tkinter version

### Notes & small differences vs Streamlit

 - Tables are shown via ttk.Treeview rather than Streamlit’s DataFrame renderer; by default, the GUI shows up to ~1000 rows per table for responsiveness (you can tweak this easily in code).
  - The per-column structured filters panel in Streamlit is condensed in Tkinter (you still get the host filter; more column filters can be added on demand).
 - The TextFSM platform map (e.g., cisco_xe → cisco_ios) is identical to your app. If you need more mappings, update DEVICE_TYPE_TO_PLATFORM in the file.
 - The launcher logic for setting NET_TEXTFSM (and fallback discovery of templates) is reproduced by set_templates_from_zip(...) + get_textfsm_index_and_dir(...). If you bundle with PyInstaller later, you can still pre-set NET_TEXTFSM or include templates alongside the executable—like your launcher’s intent. [app | Txt]

----------------------------------------------------------

## Overview

- Original script: `tk_collector_app.py` (Tkinter GUI to run a read-only command via Netmiko across hosts, preview outputs, filter/transform, and export CSV.)
- Goal: Build a **PyInstaller** executable that includes **ntc-templates** so **TextFSM** works without external setup.
- Outcome: A patched script `tk_collector_app.py` that:
  - Finds TextFSM templates automatically in multiple locations (NET_TEXTFSM, installed package, PyInstaller **one-file** `_MEIPASS`, **one-dir**, or relative path).
  - Adds a **Templates** tab to view the active templates directory and list `.textfsm` files.
  - Surfaces **per-host TextFSM reasons** when structured parsing returns no rows.
  - Provides a hint when the Structured view is empty without errors.

---

## What changed (functional diffs)

### 1) Robust template discovery
We replaced/extended `get_textfsm_index_and_dir()` to check, in order:
1. `NET_TEXTFSM` env var (if set) → `<dir>/index`
2. Installed `ntc_templates` package path → `<site-packages>/ntc_templates/templates/index`
3. **PyInstaller** data locations:
   - **one-file**: `sys._MEIPASS/ntc_templates/templates/index`
   - **one-dir**: `<dist>/<app>/ntc_templates/templates/index`
4. Relative to the script: `./ntc_templates/templates/index`

### 2) Templates tab
New **Notebook** tab **Templates** shows:
- The **templates directory** in use and whether `index` is present.
- A table listing every `.textfsm` file found (filename, inferred platform, inferred command, folder relative to templates root).
- A **Refresh** button to rescan after loading a ZIP or changing environment.

### 3) Better TextFSM diagnostics
`textfsm_parse()` now returns `(rows, reason)` and the collection loop records a per-host error like:
- `TextFSM: templates index not found`
- `TextFSM: textfsm unavailable or empty output`
- `TextFSM: textfsm error: ...`
- `TextFSM: no rows parsed (template returned zero matches)`

### 4) Hint if structured view is empty
If no structured rows are produced and there are no errors, a messagebox explains likely causes.

---

## Setup — recommended virtual environment

> Ensure your editor (e.g., VS Code) uses the same interpreter/venv that has dependencies installed.

```bash
# Windows (PowerShell)
python -m venv .venv
. .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install pandas textfsm ntc-templates netmiko pyinstaller

# macOS/Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install pandas textfsm ntc-templates netmiko pyinstaller
```

Sanity checks (run in the activated venv):
```bash
python -c "import pandas, textfsm, ntc_templates, netmiko; print('imports OK')"
python - << 'PY'
import os, ntc_templates
print(os.path.join(os.path.dirname(ntc_templates.__file__), 'templates'))
PY
```

---

## Running from source

```bash
python tk_collector_app.py
```
In the GUI:
- **Collect** tab: fill `Username`, `Password`, `device_type` (e.g., `cisco_ios`), and command (e.g., `show ip interface brief`). Browse to a hosts `.txt` file.
- **Filter & Transform**: filter lines, regex extraction, and delimiter splits.
- **Structured view (TextFSM)**: shows parsed rows if a template matches the platform/command.
- **Templates**: refresh to verify the directory and file list.
- **Download**: export CSV for filtered lines, all lines, structured results, or split columns.

---

## Building a PyInstaller executable (with ntc-templates bundled)

First, locate the templates directory:
```bash
python - << 'PY'
import os, ntc_templates
print(os.path.join(os.path.dirname(ntc_templates.__file__), 'templates'))
PY
```

### One-file build
**Windows:**
```powershell
pyinstaller --noconfirm --onefile --windowed `
  --name tk_collector_app `
  --add-data "C:\path\to\site-packages\ntc_templates\templates;ntc_templates/templates" `
  tk_collector_app_patched.py
```
**macOS/Linux:**
```bash
pyinstaller --noconfirm --onefile --windowed \
  --name tk_collector_app \
  --add-data "/path/to/site-packages/ntc_templates/templates:ntc_templates/templates" \
  tk_collector_app_patched.py
```

### One-dir build
Same command without `--onefile`.

### Optional hook (simpler command)
Create `hook-ntc_templates.py` next to your script:
```python
from PyInstaller.utils.hooks import collect_data_files

# Collect the data files of ntc_templates (the templates directory)
datas = collect_data_files('ntc_templates', include_py_files=False)
```
Then build without `--add-data`:
```bash
pyinstaller --noconfirm --onefile --windowed --name tk_collector_app tk_collector_app_patched.py
```

---

## Using the new Templates tab
- Open **Templates** → click **Refresh templates**.
- Confirm the path looks like one of:
  - `.../site-packages/ntc_templates/templates (index: present)`
  - One-file exe: a long temp dir (`_MEIPASS`)
  - One-dir exe: `dist/tk_collector_app/ntc_templates/templates`
- Check that the list shows relevant `.textfsm` files (e.g., `cisco_ios_show_ip_interface_brief.textfsm`).

---

## Troubleshooting cheatsheet

- **IDE shows "Import could not be resolved"** → Select the venv interpreter in the IDE. Reinstall packages in that venv.
- **Runtime: structured view empty** → Check **Execution errors** for a TextFSM reason; verify Templates tab shows `index: present`; confirm `device_type` → platform mapping and exact command spelling.
- **Missing modules in exe (rare)** → add hidden imports:
  ```bash
  --hidden-import netmiko --hidden-import paramiko --hidden-import textfsm
  ```
- **AV false positive (Windows one-file)** → try one-dir build or `--noupx`.

---

## Timeline / Change log

- **Step 1:** Reviewed original `tk_collector_app.py`.
- **Step 2:** Guided PyInstaller build with `--add-data` to include `ntc_templates/templates`.
- **Step 3:** Diagnosed empty Structured view; introduced explicit reasons in `textfsm_parse()` and collection loop.
- **Step 4:** Implemented robust template discovery for PyInstaller one-file/one-dir.
- **Step 5:** Added **Templates** tab with directory + file listing + refresh.
- **Step 6:** Verified IDE/venv imports and provided setup/build instructions.
- **Step 7:** Confirmed everything working end-to-end.

---

## File list

- Original script: `tk_collector_app.py`
- Optional: `hook-ntc_templates.py` (PyInstaller hook)

---

## Notes

- The platform used by TextFSM is derived from `device_type` via:
  ```python
  DEVICE_TYPE_TO_PLATFORM = {
      "cisco_ios": "cisco_ios",
      "cisco_xe": "cisco_ios",
      "cisco_nxos": "cisco_nxos",
      "cisco_asa": "cisco_asa",
      "arista_eos": "arista_eos",
      "juniper_junos": "juniper_junos",
  }
  ```
  Ensure your command string matches the NTC `index` for the platform.

- When loading a ZIP of templates, `NET_TEXTFSM` is set to the extracted folder containing `index`.

---


