
# tk_collector_app.py (patched with Templates tab & robust TextFSM discovery)
# Structured view filter works on any column via a combobox ("Any column" or a specific column).
import os
import io
import re
import time
import zipfile
import tempfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd

try:
    import textfsm
    from textfsm import clitable
    TEXTFSM_AVAILABLE = True
except Exception:
    TEXTFSM_AVAILABLE = False

# --------------------------- Core logic ---------------------------

def load_hosts_from_path(path: str) -> List[str]:
    hosts: List[str] = []
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith('#'):
                    hosts.append(s)
    except Exception:
        pass
    return hosts

def run_command(host: str, username: str, password: str, command: str, timeout: int, device_type: str) -> Tuple[str, str, str]:
    try:
        from netmiko import ConnectHandler
        from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
    except Exception as e:
        return (host, "", f"Netmiko not available: {e}")

    try:
        conn = ConnectHandler(
            device_type=device_type,
            host=host,
            username=username,
            password=password,
            timeout=timeout,
            fast_cli=True,
        )
        try:
            conn.send_command("terminal length 0", read_timeout=5)
        except Exception:
            pass
        output = conn.send_command(command, read_timeout=timeout)
        conn.disconnect()
        return (host, output, "")
    except NetmikoAuthenticationException:
        return (host, "", "Authentication failed")
    except NetmikoTimeoutException:
        return (host, "", "Timeout")
    except Exception as e:
        return (host, "", f"Error: {e}")

def df_from_raw(outputs: List[Tuple[str, str]], keep_empty_rows: bool = False) -> pd.DataFrame:
    rows = []
    now_iso = datetime.utcnow().isoformat()
    for host, output in outputs:
        lines = output.splitlines() if output else []
        if keep_empty_rows and not lines:
            rows.append({"host": host, "line_no": None, "line": "", "ts": now_iso})
        for i, line in enumerate(lines, start=1):
            rows.append({"host": host, "line_no": i, "line": line, "ts": now_iso})
    return pd.DataFrame(rows)

def safe_regex(pattern: str) -> Optional[re.Pattern]:
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None

def apply_filters(df: pd.DataFrame, host_contains: str, include_text: str, exclude_text: str, include_regex: str, exclude_regex: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if host_contains:
        out = out[out["host"].str.contains(host_contains, case=False, na=False)]
    if include_text:
        out = out[out["line"].str.contains(include_text, case=False, na=False)]
    if exclude_text:
        out = out[~out["line"].str.contains(exclude_text, case=False, na=False)]
    r_inc = safe_regex(include_regex)
    if r_inc:
        out = out[out["line"].str.contains(r_inc)]
    r_exc = safe_regex(exclude_regex)
    if r_exc:
        out = out[~out["line"].str.contains(r_exc)]
    return out

def split_columns(df_lines: pd.DataFrame, delimiter: str, maxsplit: int) -> pd.DataFrame:
    if df_lines.empty or not delimiter:
        return pd.DataFrame()
    if delimiter.lower() == "whitespace":
        parts = df_lines["line"].str.split(None, n=maxsplit if maxsplit > 0 else -1, expand=True)
    else:
        parts = df_lines["line"].str.split(delimiter, n=maxsplit if maxsplit > 0 else -1, expand=True)
    parts.columns = [f"c{i}" for i in range(parts.shape[1])]
    return pd.concat([df_lines[["host", "line_no", "line"]].reset_index(drop=True), parts], axis=1)

DEVICE_TYPE_TO_PLATFORM = {
    "cisco_ios": "cisco_ios",
    "cisco_xe": "cisco_ios",
    "cisco_nxos": "cisco_nxos",
    "cisco_asa": "cisco_asa",
    "arista_eos": "arista_eos",
    "juniper_junos": "juniper_junos",
}

def get_textfsm_index_and_dir() -> tuple[str, str]:
    """Return (index_path, templates_dir) for TextFSM templates.
    Extended to locate templates in PyInstaller one-file (sys._MEIPASS) and one-dir builds,
    in addition to NET_TEXTFSM and installed ntc_templates package.
    """
    # 1) Respect NET_TEXTFSM if set
    tdir = os.environ.get("NET_TEXTFSM", "")
    if tdir and os.path.isdir(tdir):
        index = os.path.join(tdir, "index")
        if os.path.isfile(index):
            return (index, tdir)

    # 2) Try the installed package path
    try:
        import ntc_templates, os as _os
        tdir = _os.path.join(_os.path.dirname(ntc_templates.__file__), "templates")
        index = _os.path.join(tdir, "index")
        if os.path.isdir(tdir) and os.path.isfile(index):
            return (index, tdir)
    except Exception:
        pass

    # 3) PyInstaller bundled data
    try:
        import sys
        base = getattr(sys, "_MEIPASS", None)  # one-file temp dir
        if base:
            tdir = os.path.join(base, "ntc_templates", "templates")
            index = os.path.join(tdir, "index")
            if os.path.isdir(tdir) and os.path.isfile(index):
                return (index, tdir)
        else:
            # one-dir: next to the executable
            app_dir = os.path.dirname(os.path.abspath(sys.executable))
            tdir = os.path.join(app_dir, "ntc_templates", "templates")
            index = os.path.join(tdir, "index")
            if os.path.isdir(tdir) and os.path.isfile(index):
                return (index, tdir)
    except Exception:
        pass

    # 4) Last resort: relative to script (when running from source)
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        tdir = os.path.join(here, "ntc_templates", "templates")
        index = os.path.join(tdir, "index")
        if os.path.isdir(tdir) and os.path.isfile(index):
            return (index, tdir)
    except Exception:
        pass

    return ("", "")

def set_templates_from_zip(zip_path: str) -> tuple[bool, str]:
    try:
        temp_dir = tempfile.mkdtemp(prefix="textfsm_templates_")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)
        candidate_dirs = []
        for root, dirs, files in os.walk(temp_dir):
            if "index" in files:
                candidate_dirs.append(root)
        if not candidate_dirs:
            return (False, "No 'index' file found inside the ZIP.")
        chosen = sorted(candidate_dirs, key=lambda p: len(p))[0]
        os.environ["NET_TEXTFSM"] = chosen
        return (True, chosen)
    except Exception as e:
        return (False, f"Failed to load ZIP: {e}")

def textfsm_parse(platform: str, command: str, output: str) -> Tuple[List[dict], str]:
    """Parse command output using TextFSM.
    Returns (rows, reason). 'reason' is empty on success; otherwise explains failure.
    """
    if not TEXTFSM_AVAILABLE or not output.strip():
        return ([], "textfsm unavailable or empty output")

    index, tdir = get_textfsm_index_and_dir()
    if not (index and tdir and os.path.isfile(index)):
        return ([], "templates index not found")

    try:
        cli = clitable.CliTable(index, tdir)
        attrs = {"Command": command.strip(), "Platform": platform}
        cli.ParseCmd(output, attrs)
        headers = list(cli.header)
        rows: List[dict] = [{headers[i].lower(): row[i] for i in range(len(headers))} for row in cli]
        if not rows:
            return ([], "no rows parsed (template returned zero matches)")
        return (rows, "")
    except Exception as e:
        return ([], f"textfsm error: {e}")

# --------------------------- Templates scanning helpers ---------------------------

def _infer_platform_command_from_filename(fname: str) -> tuple[str, str]:
    """
    Infer platform and command from common NTC filename like
    'cisco_ios_show_ip_interface_brief.textfsm'. Returns (platform, command).
    """
    base = os.path.basename(fname)
    stem = base[:-8] if base.endswith(".textfsm") else base
    lower = stem.lower()
    platform = ""
    command = ""
    if "_show_" in lower:
        idx = lower.index("_show_")
        platform = lower[:idx]
        command = lower[idx + 1:]  # includes 'show_...'
    else:
        parts = lower.split("_")
        if len(parts) >= 2:
            platform = parts[0]
            command = "_".join(parts[1:])
        else:
            platform = stem
            command = ""
    command_display = command.replace("_", " ").strip()
    return (platform, command_display)

def scan_templates_dataframe() -> pd.DataFrame:
    """
    Walk the templates directory returned by get_textfsm_index_and_dir(),
    collect all .textfsm files, and build a dataframe:
    columns = ['template_file', 'platform', 'command', 'folder']
    """
    index, tdir = get_textfsm_index_and_dir()
    rows = []
    if tdir and os.path.isdir(tdir):
        for root, dirs, files in os.walk(tdir):
            for f in files:
                if f.lower().endswith(".textfsm"):
                    full = os.path.join(root, f)
                    plat, cmd = _infer_platform_command_from_filename(f)
                    rows.append({
                        "template_file": f,
                        "platform": plat,
                        "command": cmd,
                        "folder": os.path.relpath(root, tdir),
                    })
    return pd.DataFrame(rows)

# --------------------------- Tkinter UI ---------------------------

class DataStore:
    def __init__(self):
        self.raw_outputs: List[Tuple[str, str]] = []
        self.errors: List[Dict[str, str]] = []
        self.structured_rows: List[Dict[str, str]] = []
        self.df_lines_all: pd.DataFrame = pd.DataFrame()
        self.df_filtered_lines: pd.DataFrame = pd.DataFrame()
        self.df_regex: pd.DataFrame = pd.DataFrame()
        self.df_split: pd.DataFrame = pd.DataFrame()
        self.df_struct: pd.DataFrame = pd.DataFrame()
        self.df_struct_filtered: pd.DataFrame = pd.DataFrame()

class TreeTable(ttk.Treeview):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._columns: List[str] = []
        self['show'] = 'headings'
        vsb = ttk.Scrollbar(master, orient="vertical", command=self.yview)
        hsb = ttk.Scrollbar(master, orient="horizontal", command=self.xview)
        self.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def set_dataframe(self, df: pd.DataFrame, max_rows: int = 1000):
        for c in self._columns:
            self.heading(c, text="")
        self.delete(*self.get_children())
        self._columns = list(df.columns)
        self['columns'] = self._columns
        for c in self._columns:
            self.heading(c, text=c)
            self.column(c, width=max(80, int(10 * (len(c) + 1))))
        if not df.empty:
            rows = df.head(max_rows).values.tolist()
            for row in rows:
                vals = [str(v) if v is not None else '' for v in row]
                self.insert('', 'end', values=vals)

class CollectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Any Show Command → Filter → CSV (Tkinter)")
        self.geometry("1200x900")
        self.store = DataStore()
        self.running = False
        self._build_ui()

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        self.tab_collect = ttk.Frame(nb)
        self.tab_filter = ttk.Frame(nb)
        self.tab_struct = ttk.Frame(nb)
        self.tab_download = ttk.Frame(nb)
        self.tab_templates = ttk.Frame(nb)  # NEW: Templates tab

        nb.add(self.tab_collect, text="Collect")
        nb.add(self.tab_filter, text="Filter & Transform")
        nb.add(self.tab_struct, text="Structured view (TextFSM)")
        nb.add(self.tab_download, text="Download")
        nb.add(self.tab_templates, text="Templates")  # add tab

        # --- Collect tab ---
        frm_top = ttk.Frame(self.tab_collect)
        frm_top.pack(fill=tk.X, padx=8, pady=8)

        conn_lab = ttk.LabelFrame(frm_top, text="Connection")
        conn_lab.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.var_username = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_device_type = tk.StringVar(value="cisco_ios")
        ttk.Label(conn_lab, text="Username").grid(row=0, column=0, sticky="w")
        ttk.Entry(conn_lab, textvariable=self.var_username, width=24).grid(row=0, column=1, sticky="we")
        ttk.Label(conn_lab, text="Password").grid(row=1, column=0, sticky="w")
        ttk.Entry(conn_lab, textvariable=self.var_password, show='•', width=24).grid(row=1, column=1, sticky="we")
        ttk.Label(conn_lab, text="device_type").grid(row=2, column=0, sticky="w")
        ttk.Entry(conn_lab, textvariable=self.var_device_type, width=24).grid(row=2, column=1, sticky="we")

        cmd_lab = ttk.LabelFrame(frm_top, text="Command")
        cmd_lab.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
        self.var_command = tk.StringVar(value="show ip interface brief")
        ttk.Label(cmd_lab, text="Show (read-only) command").grid(row=0, column=0, sticky="w")
        ttk.Entry(cmd_lab, textvariable=self.var_command, width=60).grid(row=1, column=0, sticky="we")
        self.var_timeout = tk.IntVar(value=25)
        ttk.Label(cmd_lab, text="SSH timeout (sec)").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(cmd_lab, from_=5, to=240, textvariable=self.var_timeout, width=8).grid(row=2, column=1, sticky="w")
        self.var_workers = tk.IntVar(value=30)
        ttk.Label(cmd_lab, text="Parallel workers").grid(row=2, column=2, sticky="e")
        ttk.Spinbox(cmd_lab, from_=1, to=500, textvariable=self.var_workers, width=8).grid(row=2, column=3, sticky="w")

        io_lab = ttk.LabelFrame(frm_top, text="Inputs")
        io_lab.pack(side=tk.LEFT, fill=tk.Y, expand=True, padx=4)
        self.var_hosts_path = tk.StringVar()
        ttk.Label(io_lab, text="Hosts file (.txt)").grid(row=0, column=0, sticky="w")
        ttk.Entry(io_lab, textvariable=self.var_hosts_path, width=36).grid(row=1, column=0, sticky="we")
        ttk.Button(io_lab, text="Browse…", command=self._browse_hosts).grid(row=1, column=1)
        self.var_prefer_textfsm = tk.BooleanVar(value=True)
        ttk.Checkbutton(io_lab, text="Prefer TextFSM when available", variable=self.var_prefer_textfsm).grid(row=2, column=0, sticky="w")
        self.var_templates_zip = tk.StringVar()
        ttk.Label(io_lab, text="Templates ZIP (optional)").grid(row=3, column=0, sticky="w")
        ttk.Entry(io_lab, textvariable=self.var_templates_zip, width=36).grid(row=4, column=0, sticky="we")
        ttk.Button(io_lab, text="Load ZIP…", command=self._browse_templates).grid(row=4, column=1)
        self.lbl_tpl_status = ttk.Label(io_lab, text="")
        self.lbl_tpl_status.grid(row=5, column=0, columnspan=2, sticky="w")

        act_lab = ttk.Frame(self.tab_collect)
        act_lab.pack(fill=tk.X, padx=8)
        self.btn_run = ttk.Button(act_lab, text="Run collection", command=self._start_collection)
        self.btn_run.pack(side=tk.LEFT)
        self.prog = ttk.Progressbar(act_lab, orient="horizontal", length=300, mode="determinate")
        self.prog.pack(side=tk.LEFT, padx=10)
        self.lbl_meta = ttk.Label(act_lab, text="")
        self.lbl_meta.pack(side=tk.LEFT, padx=10)

        preview_lab = ttk.LabelFrame(self.tab_collect, text="Live preview (last few devices)")
        preview_lab.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.tbl_live = TreeTable(preview_lab)

        err_lab = ttk.LabelFrame(self.tab_collect, text="Execution errors")
        err_lab.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.tbl_errors = TreeTable(err_lab)

        # --- Filter tab ---
        filt_top = ttk.Frame(self.tab_filter)
        filt_top.pack(fill=tk.X, padx=8, pady=8)
        self.var_host_contains = tk.StringVar()
        self.var_include_text = tk.StringVar()
        self.var_exclude_text = tk.StringVar()
        self.var_include_regex = tk.StringVar()
        self.var_exclude_regex = tk.StringVar()
        ttk.Label(filt_top, text="Host contains").grid(row=0, column=0, sticky="w")
        ttk.Entry(filt_top, textvariable=self.var_host_contains, width=18).grid(row=1, column=0)
        ttk.Label(filt_top, text="Output contains").grid(row=0, column=1, sticky="w")
        ttk.Entry(filt_top, textvariable=self.var_include_text, width=18).grid(row=1, column=1)
        ttk.Label(filt_top, text="Output NOT contains").grid(row=0, column=2, sticky="w")
        ttk.Entry(filt_top, textvariable=self.var_exclude_text, width=18).grid(row=1, column=2)
        ttk.Label(filt_top, text="Include regex (optional)").grid(row=0, column=3, sticky="w")
        ttk.Entry(filt_top, textvariable=self.var_include_regex, width=18).grid(row=1, column=3)
        ttk.Label(filt_top, text="Exclude regex (optional)").grid(row=0, column=4, sticky="w")
        ttk.Entry(filt_top, textvariable=self.var_exclude_regex, width=18).grid(row=1, column=4)
        ttk.Button(filt_top, text="Apply filters", command=self._apply_filters).grid(row=1, column=5, padx=8)

        ttk.Label(self.tab_filter, text="Preview (line view)").pack(anchor="w", padx=8)
        self.tbl_lines = TreeTable(self.tab_filter)

        regex_lab = ttk.LabelFrame(self.tab_filter, text="🔬 Optional: Regex → Columns (named capture groups)")
        regex_lab.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.txt_regex = tk.Text(regex_lab, height=6)
        self.txt_regex.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(regex_lab, text="Extract with regex", command=self._apply_regex_extract).pack(anchor="w", padx=4, pady=4)
        self.tbl_regex = TreeTable(regex_lab)

        split_lab = ttk.LabelFrame(self.tab_filter, text="🧪 Optional: Split columns (delimiter)")
        split_lab.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.var_delim = tk.StringVar(value="whitespace")
        self.var_maxsplit = tk.IntVar(value=0)
        frm_split = ttk.Frame(split_lab)
        frm_split.pack(fill=tk.X)
        ttk.Label(frm_split, text="Delimiter (use 'whitespace' for spaces/tabs)").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_split, textvariable=self.var_delim, width=24).grid(row=0, column=1)
        ttk.Label(frm_split, text="Max splits (0 = unlimited)").grid(row=0, column=2, sticky="e")
        ttk.Spinbox(frm_split, from_=0, to=100, textvariable=self.var_maxsplit, width=6).grid(row=0, column=3)
        ttk.Button(frm_split, text="Split", command=self._apply_split).grid(row=0, column=4, padx=8)
        self.tbl_split = TreeTable(split_lab)

        # --- Structured tab ---
        struct_top = ttk.Frame(self.tab_struct)
        struct_top.pack(fill=tk.X, padx=8, pady=8)
        # Search value and column selector
        self.var_struct_search = tk.StringVar()
        ttk.Label(struct_top, text="Contains (structured)").grid(row=0, column=0, sticky="w")
        ttk.Entry(struct_top, textvariable=self.var_struct_search, width=24).grid(row=0, column=1)
        ttk.Label(struct_top, text="Column").grid(row=0, column=2, sticky="e")
        self.var_struct_column = tk.StringVar(value="Any column")
        self.cbo_struct_column = ttk.Combobox(struct_top, textvariable=self.var_struct_column, state="readonly", width=20)
        self.cbo_struct_column['values'] = ("Any column",)
        self.cbo_struct_column.grid(row=0, column=3, sticky="w")
        ttk.Button(struct_top, text="Apply", command=self._apply_struct_filters).grid(row=0, column=4, padx=8)
        self.tbl_struct = TreeTable(self.tab_struct)

        # --- Downloads tab ---
        down_lab = ttk.LabelFrame(self.tab_download, text="Download best available")
        down_lab.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(down_lab, text="⬇️ FILTERED (line view) CSV", command=self._download_filtered_lines).pack(anchor="w", padx=4, pady=4)
        ttk.Button(down_lab, text="⬇️ ALL (unfiltered lines) CSV", command=self._download_all_lines).pack(anchor="w", padx=4, pady=4)
        ttk.Button(down_lab, text="⬇️ STRUCTURED CSV (best available)", command=self._download_structured_best).pack(anchor="w", padx=4, pady=4)

        # --- Templates tab ---
        tpl_top = ttk.Frame(self.tab_templates)
        tpl_top.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(tpl_top, text="Templates directory in use:").grid(row=0, column=0, sticky="w")
        self.lbl_templates_dir = ttk.Label(tpl_top, text="(not detected)")
        self.lbl_templates_dir.grid(row=0, column=1, sticky="w")
        ttk.Button(tpl_top, text="Refresh templates", command=self._refresh_templates_tab).grid(row=0, column=2, padx=8)
        self.tbl_templates = TreeTable(self.tab_templates)

    # --------------------- File pickers ---------------------
    def _browse_hosts(self):
        path = filedialog.askopenfilename(title="Select hosts .txt", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.var_hosts_path.set(path)

    def _browse_templates(self):
        path = filedialog.askopenfilename(title="Select templates .zip", filetypes=[("ZIP", "*.zip"), ("All", "*.*")])
        if path:
            self.var_templates_zip.set(path)
            ok, msg = set_templates_from_zip(path)
            if ok:
                self.lbl_tpl_status.configure(text=f"Templates loaded from: {os.environ.get('NET_TEXTFSM')}")
                # Refresh templates tab view
                self._refresh_templates_tab()
            else:
                self.lbl_tpl_status.configure(text=msg)
                messagebox.showwarning("Templates", msg)

    # --------------------- Collection ---------------------
    def _start_collection(self):
        if self.running:
            return
        if not self.var_hosts_path.get().strip():
            messagebox.showerror("Inputs", "Please select a hosts file (.txt).")
            return
        if not self.var_username.get().strip() or not self.var_password.get().strip():
            messagebox.showerror("Inputs", "Please provide username and password.")
            return
        hosts = load_hosts_from_path(self.var_hosts_path.get())
        if not hosts:
            messagebox.showerror("Inputs", "No hosts found in file.")
            return

        # reset store
        self.store.raw_outputs = []
        self.store.errors = []
        self.store.structured_rows = []
        self.store.df_lines_all = pd.DataFrame()
        self.store.df_filtered_lines = pd.DataFrame()
        self.store.df_regex = pd.DataFrame()
        self.store.df_split = pd.DataFrame()
        self.store.df_struct = pd.DataFrame()
        self.store.df_struct_filtered = pd.DataFrame()

        # reset UI tables
        self.tbl_live.set_dataframe(pd.DataFrame())
        self.tbl_errors.set_dataframe(pd.DataFrame())

        self.running = True
        self.btn_run.configure(state=tk.DISABLED)
        self.prog.configure(value=0, maximum=len(hosts))
        self.lbl_meta.configure(text="Starting…")

        def worker():
            start = time.time()
            platform = DEVICE_TYPE_TO_PLATFORM.get(self.var_device_type.get(), self.var_device_type.get())
            with ThreadPoolExecutor(max_workers=int(self.var_workers.get())) as executor:
                futures = {
                    executor.submit(
                        run_command,
                        h,
                        self.var_username.get(),
                        self.var_password.get(),
                        self.var_command.get().strip(),
                        int(self.var_timeout.get()),
                        self.var_device_type.get(),
                    ): h
                    for h in hosts
                }
                done = 0
                for fut in as_completed(futures):
                    host, output, err = fut.result()
                    if err:
                        self.store.errors.append({"host": host, "error": err})
                    else:
                        self.store.raw_outputs.append((host, output))
                        if self.var_prefer_textfsm.get():
                            rows, reason = textfsm_parse(platform, self.var_command.get(), output)
                            if rows:
                                for r in rows:
                                    self.store.structured_rows.append({"host": host, **r})
                            else:
                                # Surface why structured view is empty for this host
                                self.store.errors.append({"host": host, "error": f"TextFSM: {reason}"})
                    done += 1
                    self._update_progress(done, len(hosts))
            elapsed = time.time() - start
            self._complete_collection(elapsed, len(hosts))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, done: int, total: int):
        def _do():
            self.prog.configure(value=done)
            self.lbl_meta.configure(text=f"Processed {done}/{total}")
            if self.store.raw_outputs:
                df_live = df_from_raw(self.store.raw_outputs[-5:], keep_empty_rows=True)
                self.tbl_live.set_dataframe(df_live.tail(100))
            if self.store.errors:
                self.tbl_errors.set_dataframe(pd.DataFrame(self.store.errors))
        self.after(0, _do)

    def _complete_collection(self, elapsed: float, host_count: int):
        def _do():
            self.running = False
            self.btn_run.configure(state=tk.NORMAL)
            self.lbl_meta.configure(text=(
                f"Processed {host_count} hosts in {elapsed:.1f}s. "
                f"Outputs: {len(self.store.raw_outputs)}. "
                f"Structured rows: {len(self.store.structured_rows)}."
            ))
            self.prog.configure(value=self.prog['maximum'])

            # Build dataframes
            self.store.df_lines_all = df_from_raw(self.store.raw_outputs, keep_empty_rows=True)
            self.tbl_lines.set_dataframe(self.store.df_lines_all.head(1000))

            self.store.df_struct = pd.DataFrame(self.store.structured_rows)
            if not self.store.df_struct.empty:
                cols = list(self.store.df_struct.columns)
                self.cbo_struct_column['values'] = ("Any column",) + tuple(cols)
                if self.var_struct_column.get() not in self.cbo_struct_column['values']:
                    self.var_struct_column.set("Any column")
                self.tbl_struct.set_dataframe(self.store.df_struct.head(2000))
            else:
                # Helpful hint when structured view is empty
                if not self.store.errors:
                    messagebox.showinfo(
                        "TextFSM",
                        "Structured view is empty.\n"
                        "No matching template in index for the given Platform/Command "
                        "or the parser returned no rows."
                    )

            # Update templates tab summary and status label
            idx, tdir = get_textfsm_index_and_dir()
            if tdir:
                self.lbl_tpl_status.configure(text=f"Templates in use: {tdir}")
            else:
                self.lbl_tpl_status.configure(text="Templates not found")
            self._refresh_templates_tab()
        self.after(0, _do)

    # --------------------- Filters & transforms ---------------------
    def _apply_filters(self):
        df_all = self.store.df_lines_all
        self.store.df_filtered_lines = apply_filters(
            df_all,
            self.var_host_contains.get(),
            self.var_include_text.get(),
            self.var_exclude_text.get(),
            self.var_include_regex.get(),
            self.var_exclude_regex.get(),
        )
        self.tbl_lines.set_dataframe(self.store.df_filtered_lines.head(1000))

    def _apply_regex_extract(self):
        pattern = self.txt_regex.get("1.0", tk.END).strip()
        df_src = self.store.df_filtered_lines if not self.store.df_filtered_lines.empty else self.store.df_lines_all
        if not pattern:
            messagebox.showinfo("Regex", "Please provide a named-group regex pattern.")
            return
        try:
            matches = df_src["line"].str.extract(pattern, flags=re.IGNORECASE)
            if not matches.empty:
                df_regex = pd.concat([df_src[["host", "line_no", "line"]].reset_index(drop=True), matches], axis=1)
                non_null = matches.notna().any(axis=1)
                df_regex = df_regex[non_null]
                self.store.df_regex = df_regex
                self.tbl_regex.set_dataframe(df_regex.head(1000))
            else:
                messagebox.showwarning("Regex", "Regex valid but no lines matched.")
        except re.error as rex:
            messagebox.showerror("Regex", f"Invalid regex: {rex}")

    def _apply_split(self):
        df_src = self.store.df_filtered_lines if not self.store.df_filtered_lines.empty else self.store.df_lines_all
        df_split = split_columns(df_src, self.var_delim.get(), int(self.var_maxsplit.get())) if self.var_delim.get().strip() else pd.DataFrame()
        self.store.df_split = df_split
        self.tbl_split.set_dataframe(df_split.head(1000))

    def _apply_struct_filters(self):
        df_struct = self.store.df_struct
        if df_struct.empty:
            return
        df = df_struct.copy()
        search_val = self.var_struct_search.get().strip()
        column = self.var_struct_column.get().strip()
        if search_val:
            if column and column != "Any column" and column in df.columns:
                df = df[df[column].astype(str).str.contains(search_val, case=False, na=False)]
            else:
                mask = False
                for c in df.columns:
                    mask = mask | df[c].astype(str).str.contains(search_val, case=False, na=False)
                df = df[mask]
        self.store.df_struct_filtered = df
        self.tbl_struct.set_dataframe(df.head(2000))

    # --------------------- Downloads ---------------------
    def _save_df_csv(self, df: pd.DataFrame, default_name: str):
        if df is None or df.empty:
            messagebox.showwarning("Download", "No data available to export.")
            return
        path = filedialog.asksaveasfilename(title="Save CSV", defaultextension=".csv", initialfile=default_name,
                                            filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if not path:
            return
        try:
            df.to_csv(path, index=False)
            messagebox.showinfo("Download", f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Download", f"Failed to save: {e}")

    def _download_filtered_lines(self):
        src = self.store.df_filtered_lines if not self.store.df_filtered_lines.empty else self.store.df_lines_all
        self._save_df_csv(src, "show_results_filtered_lines.csv")

    def _download_all_lines(self):
        self._save_df_csv(self.store.df_lines_all, "show_results_all_lines.csv")

    def _download_structured_best(self):
        if not self.store.df_struct.empty:
            src = self.store.df_struct_filtered if not self.store.df_struct_filtered.empty else self.store.df_struct
            name = "show_results_textfsm_structured.csv"
        elif not self.store.df_regex.empty:
            src = self.store.df_regex
            name = "show_results_regex_structured.csv"
        elif not self.store.df_split.empty:
            src = self.store.df_split
            name = "show_results_split_columns.csv"
        else:
            src = self.store.df_filtered_lines if not self.store.df_filtered_lines.empty else self.store.df_lines_all
            name = "show_results_filtered_lines.csv"
        self._save_df_csv(src, name)

    # --------------------- Templates tab logic ---------------------
    def _refresh_templates_tab(self):
        idx, tdir = get_textfsm_index_and_dir()
        if tdir:
            present = os.path.isfile(idx)
            self.lbl_templates_dir.configure(text=f"{tdir} (index: {'present' if present else 'missing'})")
        else:
            self.lbl_templates_dir.configure(text="(not detected)")
        try:
            df = scan_templates_dataframe()
        except Exception as e:
            messagebox.showerror("Templates", f"Failed to scan templates: {e}")
            df = pd.DataFrame()
        if df.empty:
            df = pd.DataFrame([{ "template_file": "(none found)", "platform": "", "command": "", "folder": "" }])
        self.tbl_templates.set_dataframe(df.head(5000))

def main():
    app = CollectorApp()
    # Initial templates tab population
    app._refresh_templates_tab()
    app.mainloop()

if __name__ == '__main__':
    main()
