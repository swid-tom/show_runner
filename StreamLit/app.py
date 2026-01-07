#!/usr/bin/env python3
import os
import io
import re
import csv
import time
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional

import streamlit as st
import pandas as pd

# Optional TextFSM imports (handled gracefully if missing)
try:
    import textfsm
    from textfsm import clitable
    TEXTFSM_AVAILABLE = True
except Exception:
    TEXTFSM_AVAILABLE = False

# --------------------- Streamlit Page ---------------------
st.set_page_config(page_title="Any Show Command → Filter → CSV (with TextFSM)", layout="wide")
st.title("📥 Collect Any Show Command → 🔎 Filter → ⬇️ CSV")
st.caption("Collect first, filter after. Optional TextFSM parsing for structured CSV.")

# --------------------- Helpers ---------------------
def load_hosts_from_buf(buf: io.StringIO) -> List[str]:
    buf.seek(0)
    return [line.strip() for line in buf if line.strip() and not line.strip().startswith("#")]

def run_command(
    host: str,
    username: str,
    password: str,
    command: str,
    timeout: int,
    device_type: str,
) -> Tuple[str, str, str]:
    """
    Returns (host, output, error). 'error' empty when success.
    """
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

def df_from_raw(outputs: List[Tuple[str, str]], keep_empty_rows=False) -> pd.DataFrame:
    """
    Build a line-oriented dataframe:
      columns: host, line_no, line, ts
    """
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

def apply_filters(
    df: pd.DataFrame,
    host_contains: str,
    include_text: str,
    exclude_text: str,
    include_regex: str,
    exclude_regex: str,
) -> pd.DataFrame:
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
    """
    Split line into columns by delimiter (supports 'whitespace' keyword).
    """
    if df_lines.empty or not delimiter:
        return pd.DataFrame()
    if delimiter.lower() == "whitespace":
        parts = df_lines["line"].str.split(None, n=maxsplit if maxsplit > 0 else -1, expand=True)
    else:
        parts = df_lines["line"].str.split(delimiter, n=maxsplit if maxsplit > 0 else -1, expand=True)
    parts.columns = [f"c{i}" for i in range(parts.shape[1])]
    return pd.concat([df_lines[["host", "line_no", "line"]].reset_index(drop=True), parts], axis=1)

def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    if df.empty:
        return "".encode("utf-8")
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

# ---------- TextFSM utilities ----------
DEVICE_TYPE_TO_PLATFORM = {
    "cisco_ios": "cisco_ios",
    "cisco_xe": "cisco_ios",       # IOS-XE often uses IOS templates
    "cisco_nxos": "cisco_nxos",
    "cisco_asa": "cisco_asa",
    "arista_eos": "arista_eos",
    "juniper_junos": "juniper_junos",
    # extend as needed
}

def get_textfsm_index_and_dir() -> tuple[str, str]:
    """
    Returns (index_file, templates_dir) for TextFSM.
    Prefers NET_TEXTFSM; falls back to ntc_templates package if present.
    """
    tdir = os.environ.get("NET_TEXTFSM", "")
    if tdir and os.path.isdir(tdir):
        index = os.path.join(tdir, "index")
        if os.path.isfile(index):
            return (index, tdir)

    # Fallback to ntc_templates
    try:
        import ntc_templates, os as _os
        tdir = _os.path.join(_os.path.dirname(ntc_templates.__file__), "templates")
        index = _os.path.join(tdir, "index")
        if os.path.isdir(tdir) and os.path.isfile(index):
            return (index, tdir)
    except Exception:
        pass

    return ("", "")

def set_templates_from_zip(zip_bytes: bytes) -> tuple[bool, str]:
    """
    Accept a .zip uploaded by the user with TextFSM templates (including an 'index' file),
    extract to a temp dir, set NET_TEXTFSM accordingly, and return (ok, path).
    """
    try:
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix="textfsm_templates_")
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(temp_dir)
        # Common layout: zip may contain a folder 'templates/' or template files at root
        # If a 'templates' subdir exists with an 'index', prefer that.
        candidate_dirs = []
        for root, dirs, files in os.walk(temp_dir):
            if "index" in files:
                candidate_dirs.append(root)
        # Choose the shortest path containing 'index'
        if not candidate_dirs:
            return (False, "No 'index' file found inside the ZIP.")
        chosen = sorted(candidate_dirs, key=lambda p: len(p))[0]
        os.environ["NET_TEXTFSM"] = chosen
        return (True, chosen)
    except Exception as e:
        return (False, f"Failed to load ZIP: {e}")

def textfsm_has_template(platform: str, command: str) -> bool:
    """
    Quickly check if a template exists for (platform, command).
    Tries parsing an empty string. If template is missing, CliTableError is raised.
    """
    if not TEXTFSM_AVAILABLE:
        return False
    index, tdir = get_textfsm_index_and_dir()
    if not (index and os.path.isfile(index)):
        return False
    try:
        cli = clitable.CliTable(index, tdir)
        attrs = {"Command": command.strip(), "Platform": platform}
        # Attempt parse with empty output; if template exists, will not raise
        cli.ParseCmd("", attrs)
        return True
    except Exception:
        return False

def textfsm_parse(platform: str, command: str, output: str) -> List[dict]:
    """
    Parse a command output with TextFSM (ntc-templates).
    Returns a list of dicts (structured rows) or empty list if no match.
    """
    if not TEXTFSM_AVAILABLE or not output.strip():
        return []
    index, tdir = get_textfsm_index_and_dir()
    if not (index and tdir and os.path.isfile(index)):
        return []
    try:
        cli = clitable.CliTable(index, tdir)
        attrs = {"Command": command.strip(), "Platform": platform}
        cli.ParseCmd(output, attrs)
        headers = list(cli.header)
        rows: List[dict] = []
        for row in cli:
            rows.append({headers[i].lower(): row[i] for i in range(len(headers))})
        return rows
    except Exception:
        return []

# --------------------- Sidebar (Connection & Options) ---------------------
with st.sidebar:
    st.header("Connection")
    username = st.text_input("Username", value="", placeholder="network.ops")
    password = st.text_input("Password", type="password", value="", placeholder="••••••••")

    st.caption("Netmiko device_type (e.g., cisco_ios, cisco_nxos, cisco_asa, arista_eos, juniper_junos)")
    device_type = st.text_input("device_type", value="cisco_ios")

    st.header("Command")
    command = st.text_area("Show (read-only) command", value="show ip interface brief", height=80,
                           help="Use canonical command text (avoid '| include ...' if you want TextFSM to match).")
    timeout = st.number_input("SSH timeout (sec)", min_value=5, max_value=240, value=25, step=1)
    workers = st.number_input("Parallel workers", min_value=1, max_value=500, value=30, step=1)

    st.header("Hosts")
    hosts_file = st.file_uploader("Upload hosts file (.txt, one host/IP per line)", type=["txt"])

    st.header("TextFSM (Structured Parsing)")
    prefer_textfsm = st.checkbox("Prefer TextFSM when available", value=True)
    templates_zip = st.file_uploader("Optional: Upload TextFSM templates ZIP", type=["zip"],
                                     help="ZIP containing an 'index' file and .template files")

    template_status_placeholder = st.empty()

    run_btn = st.button(
        "Run collection",
        type="primary",
        disabled=not hosts_file or not username or not password or not command.strip(),
    )

# Handle custom templates ZIP
if templates_zip is not None:
    ok, msg = set_templates_from_zip(templates_zip.getvalue())
    if ok:
        st.sidebar.success(f"Templates loaded from: {os.environ.get('NET_TEXTFSM')}")
    else:
        st.sidebar.error(msg)

# Pre-check template availability (best-effort)
platform = DEVICE_TYPE_TO_PLATFORM.get(device_type, device_type)
if prefer_textfsm and TEXTFSM_AVAILABLE:
    has_tpl = textfsm_has_template(platform, command)
    if has_tpl:
        template_status_placeholder.success(f"Template found for platform='{platform}', command='{command.strip()}'")
    else:
        template_status_placeholder.info("No matching TextFSM template detected (will fall back to raw/regex/split).")
else:
    if not TEXTFSM_AVAILABLE and prefer_textfsm:
        template_status_placeholder.warning("textfsm/ntc-templates not installed; structured parsing disabled.")

st.markdown(
    """
**How it works:** We connect to each host, run your command, store the **entire raw output**,
then you filter below. If a TextFSM template matches, you'll also get a **structured table** for clean CSV export.
"""
)

# --------------------- Session State ---------------------
if "raw_outputs" not in st.session_state:
    st.session_state.raw_outputs: List[Tuple[str, str]] = []
if "errors" not in st.session_state:
    st.session_state.errors: List[Dict[str, str]] = []
if "structured_rows" not in st.session_state:
    st.session_state.structured_rows: List[Dict[str, str]] = []

# --------------------- Run Collection ---------------------
meta_placeholder = st.empty()
progress_placeholder = st.empty()
live_preview_placeholder = st.empty()
errors_expander = st.expander("Execution errors", expanded=False)

if run_btn and hosts_file:
    try:
        # Reset
        st.session_state.raw_outputs = []
        st.session_state.errors = []
        st.session_state.structured_rows = []

        hosts_buf = io.StringIO(hosts_file.getvalue().decode("utf-8", errors="ignore"))
        hosts = load_hosts_from_buf(hosts_buf)
        if not hosts:
            st.error("No hosts found in file.")
        else:
            start = time.time()
            progress = st.progress(0, text="Starting…")

            with ThreadPoolExecutor(max_workers=int(workers)) as executor:
                futures = {
                    executor.submit(run_command, h, username, password, command.strip(), int(timeout), device_type): h
                    for h in hosts
                }
                total = len(futures)
                done = 0
                last_render = 0.0

                for fut in as_completed(futures):
                    host, output, err = fut.result()
                    if err:
                        st.session_state.errors.append({"host": host, "error": err})
                    else:
                        st.session_state.raw_outputs.append((host, output))

                        # Optional TextFSM parse
                        if prefer_textfsm:
                            parsed_rows = textfsm_parse(platform, command, output)
                            for row in parsed_rows:
                                st.session_state.structured_rows.append({"host": host, **row})

                    done += 1
                    progress.progress(done / total, text=f"Processed {done}/{total}")

                    # Lightweight live preview (last few devices)
                    now = time.time()
                    if now - last_render > 0.7 and st.session_state.raw_outputs:
                        df_live = df_from_raw(st.session_state.raw_outputs[-5:], keep_empty_rows=True)
                        live_preview_placeholder.dataframe(df_live.tail(100), use_container_width=True, height=240)
                        last_render = now

            elapsed = time.time() - start
            progress_placeholder.empty()
            progress.empty()
            meta_placeholder.info(
                f"Processed **{len(hosts)}** hosts in **{elapsed:.1f}s**. "
                f"Collected outputs from **{len(st.session_state.raw_outputs)}** devices. "
                f"Structured rows: **{len(st.session_state.structured_rows)}**."
            )

            if st.session_state.errors:
                with errors_expander:
                    st.dataframe(pd.DataFrame(st.session_state.errors), use_container_width=True)

    except Exception as e:
        st.exception(e)

# --------------------- Post-collection: Filter & Transform ---------------------
st.subheader("Filter & Transform")

# Line-oriented DF (always available)
df_lines_all = df_from_raw(st.session_state.raw_outputs, keep_empty_rows=True)
st.caption(f"Total lines collected: **{len(df_lines_all):,}**")

# Quick filters
f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 2, 2])
with f1:
    host_contains = st.text_input("Host contains", value="")
with f2:
    include_text = st.text_input("Output contains", value="")
with f3:
    exclude_text = st.text_input("Output NOT contains", value="")
with f4:
    include_regex = st.text_input("Include regex (optional)", value="", placeholder=r"e.g. ^Gi\d+/\d+")
with f5:
    exclude_regex = st.text_input("Exclude regex (optional)", value="", placeholder=r"e.g. administratively\s+down")

df_filtered_lines = apply_filters(
    df_lines_all, host_contains, include_text, exclude_text, include_regex, exclude_regex
)

st.markdown("**Preview (line view):**")
st.dataframe(df_filtered_lines.head(1000), use_container_width=True)

# Optional: regex → columns (fallback when no TextFSM template)
with st.expander("🔬 Optional: Regex → Columns (named capture groups)"):
    st.markdown(
        """
        Provide a **named-group** regex to turn lines into structured columns.
        Example for `show ip int br`:
        ```
        ^(?P<interface>\S+)\s+(?P<ip>\S+)\s+(?P<ok>\S+)\s+(?P<method>\S+)\s+(?P<status>.+)\s+(?P<protocol>\S+)$
        ```
        Only matching lines will populate the table below.
        """
    )
    regex_pattern = st.text_area("Named-group regex", value="", height=120)
    if regex_pattern.strip():
        try:
            matches = df_filtered_lines["line"].str.extract(regex_pattern, flags=re.IGNORECASE)
            if not matches.empty:
                df_regex = pd.concat(
                    [df_filtered_lines[["host", "line_no", "line"]].reset_index(drop=True), matches],
                    axis=1
                )
                non_null = matches.notna().any(axis=1)
                df_regex = df_regex[non_null]
                st.success(f"Extracted columns: {', '.join([c for c in matches.columns if c])}")
                st.dataframe(df_regex.head(1000), use_container_width=True)
            else:
                st.warning("Regex valid but no lines matched.")
        except re.error as rex:
            st.error(f"Invalid regex: {rex}")
    else:
        df_regex = pd.DataFrame()

# Optional: split columns (delimiter)
with st.expander("🪚 Optional: Split columns (delimiter)"):
    d1, d2 = st.columns([2, 1])
    with d1:
        delimiter = st.text_input("Delimiter (use 'whitespace' for any spaces/tabs)", value="whitespace")
    with d2:
        maxsplit = st.number_input("Max splits (0 = unlimited)", min_value=0, max_value=100, value=0, step=1)
    df_split = split_columns(df_filtered_lines, delimiter, maxsplit) if delimiter.strip() else pd.DataFrame()
    if not df_split.empty:
        st.dataframe(df_split.head(1000), use_container_width=True)

# --------------------- TextFSM Structured View ---------------------
st.subheader("Structured view (TextFSM)")

if not TEXTFSM_AVAILABLE and prefer_textfsm:
    st.info("Install packages to enable TextFSM parsing:\n\n`pip install textfsm ntc-templates`")

df_struct = pd.DataFrame(st.session_state.structured_rows) if st.session_state.structured_rows else pd.DataFrame()

if not df_struct.empty:
    # Simple per-column contains filters
    with st.expander("Column filters (structured)"):
        cols = [c for c in df_struct.columns if c != "host"]  # 'host' is common; keep separate
        with st.container():
            host_filter_struct = st.text_input("Host contains (structured)", value="")
            col_filters = {}
            for c in cols:
                val = st.text_input(f"Contains → {c}", value="")
                if val.strip():
                    col_filters[c] = val.strip()

    df_struct_filtered = df_struct.copy()
    if host_filter_struct:
        df_struct_filtered = df_struct_filtered[
            df_struct_filtered["host"].astype(str).str.contains(host_filter_struct, case=False, na=False)
        ]
    for c, v in (col_filters if 'col_filters' in locals() else {}).items():
        df_struct_filtered = df_struct_filtered[
            df_struct_filtered[c].astype(str).str.contains(v, case=False, na=False)
        ]

    st.dataframe(df_struct_filtered.head(2000), use_container_width=True)
else:
    st.info("No structured rows available (no matching TextFSM template or parsing returned zero rows).")

# --------------------- Downloads ---------------------
st.subheader("Download")

c1, c2, c3 = st.columns([1, 1, 1])

with c1:
    st.download_button(
        "⬇️ FILTERED (line view) CSV",
        data=to_csv_bytes(df_filtered_lines),
        file_name="show_results_filtered_lines.csv",
        mime="text/csv",
        disabled=df_filtered_lines.empty,
        help="Exports the filtered line-oriented view (host, line_no, line).",
    )

with c2:
    st.download_button(
        "⬇️ ALL (unfiltered lines) CSV",
        data=to_csv_bytes(df_lines_all),
        file_name="show_results_all_lines.csv",
        mime="text/csv",
        disabled=df_lines_all.empty,
        help="Exports every collected line from all hosts.",
    )

with c3:
    if not df_struct.empty:
        data = to_csv_bytes(df_struct_filtered if 'df_struct_filtered' in locals() else df_struct)
        name = "show_results_textfsm_structured.csv"
        disabled = False
        tip = "Exports the TextFSM-parsed table (preferred when templates exist)."
    elif 'df_regex' in locals() and not df_regex.empty:
        data = to_csv_bytes(df_regex)
        name = "show_results_regex_structured.csv"
        disabled = False
        tip = "No TextFSM result; exporting the regex-structured table."
    elif 'df_split' in locals() and not df_split.empty:
        data = to_csv_bytes(df_split)
        name = "show_results_split_columns.csv"
        disabled = False
        tip = "No TextFSM/regex result; exporting the split-columns table."
    else:
        data = to_csv_bytes(df_filtered_lines)
        name = "show_results_filtered_lines.csv"
        disabled = df_filtered_lines.empty
        tip = "No structured table available; exporting the filtered line view."

    st.download_button(
        "⬇️ STRUCTURED CSV (best available)",
        data=data,
        file_name=name,
        mime="text/csv",
        disabled=disabled,
        help=tip,
    )

# --------------------- Footer tips ---------------------
with st.expander("Tips & Notes"):
    st.markdown(
        """
- **TextFSM matching** uses exact `Command` + `Platform`. Avoid appending IOS filters (e.g., `| include`) to the command;
  apply those in the Streamlit filters instead.
- **Platform mapping:** The app maps `device_type` → TextFSM platform (e.g., `cisco_xe` → `cisco_ios`). Adjust `DEVICE_TYPE_TO_PLATFORM` if needed.
- **Custom templates:** Upload a ZIP that includes an `index` file and `.template` files. The app sets `NET_TEXTFSM` for this session.
- If you frequently run **multiple commands**, we can extend the app to accept multi-line commands and tag results with a `command` column.
"""
    )