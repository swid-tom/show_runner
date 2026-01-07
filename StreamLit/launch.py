# launch.py
import os
import sys
import socket
import logging
import tempfile
import webbrowser
from contextlib import closing
from time import sleep

LOG_PATH = os.path.join(tempfile.gettempdir(), "CellularCollector.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
console = logging.getLogger("launcher")

def find_free_port(start: int = 8501, end: int = 8600) -> int:
    """Find a free localhost TCP port."""
    for port in range(start, end + 1):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 8501  # fallback (may still be occupied)

def resolved_base_dir() -> str:
    """
    Return the directory where bundled files live at runtime.
    In --onefile mode, PyInstaller extracts to sys._MEIPASS.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))

def ensure_net_textfsm() -> None:
    """
    Set NET_TEXTFSM so TextFSM can find templates (ntc_templates/templates).
    Works when templates were bundled with PyInstaller (collect-all).
    """
    try:
        # Prefer package resources (works in frozen apps with PyInstaller collect-data/all)
        import importlib.resources as pkg_resources
        import ntc_templates
        with pkg_resources.path(ntc_templates, "templates") as tpl_path:
            os.environ["NET_TEXTFSM"] = str(tpl_path)
            console.debug(f"NET_TEXTFSM={os.environ['NET_TEXTFSM']}")
            return
    except Exception as e:
        console.warning(f"Package templates not resolved: {e}")

    # Fallback to a local 'templates' next to the executable
    candidate = os.path.join(resolved_base_dir(), "templates")
    if os.path.isdir(candidate):
        os.environ["NET_TEXTFSM"] = candidate
        console.debug(f"NET_TEXTFSM (local)={candidate}")
    else:
        console.error("TextFSM templates not found; structured parsing may be unavailable.")

def force_production_mode() -> None:
    """
    Disable Streamlit development mode so --server.port is allowed.
    Do this BEFORE importing/running Streamlit.
    """
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENTMODE"] = "false"

    # Provide an explicit config path to avoid user overrides
    cfg_dir = os.path.join(tempfile.gettempdir(), "st_cfg_cc")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, "config.toml")
    if not os.path.isfile(cfg_path):
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("[global]\n")
            f.write("developmentMode = false\n")
            f.write("[browser]\n")
            f.write("gatherUsageStats = false\n")
    os.environ["STREAMLIT_CONFIG"] = cfg_path
    console.debug(f"Production mode set; STREAMLIT_CONFIG={cfg_path}")

def main() -> None:
    try:
        console.debug("Launcher starting…")
        base = resolved_base_dir()
        app_path = os.path.join(base, "app.py")
        if not os.path.isfile(app_path):
            print(f"[ERROR] app.py not found at: {app_path}")
            print(f"See log: {LOG_PATH}")
            sys.exit(1)

        ensure_net_textfsm()
        force_production_mode()

        port = find_free_port()
        url = f"http://127.0.0.1:{port}"
        console.debug(f"Using URL {url}")

        # Choose Streamlit CLI entry point that exists in your version
        try:
            from streamlit.web.cli import main as st_main
            console.debug("Using streamlit.web.cli.main")
        except Exception:
            from streamlit.cli import main as st_main  # type: ignore
            console.debug("Using streamlit.cli.main (legacy)")

        # Build argv as if called via CLI
        sys.argv = [
            "streamlit", "run", app_path,
            "--global.developmentMode=false",
            "--server.address=127.0.0.1",
            f"--server.port={port}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ]

        print(f"[CellularCollector] Starting Streamlit at {url}")
        print(f"Log -> {LOG_PATH}")

        # Try to open browser shortly after start
        def delayed_open() -> None:
            for _ in range(6):
                try:
                    webbrowser.open(url)
                    break
                except Exception:
                    sleep(0.5)

        delayed_open()
        st_main()

    except SystemExit as se:
        # Normal Streamlit shutdown uses sys.exit
        code = se.code if isinstance(se.code, int) else 0
        console.info(f"Streamlit exited with code {code}")
        sys.exit(code)
    except Exception as e:
        console.exception("Failed to start Streamlit")
        print(f"[ERROR] Failed to start Streamlit: {e}")
        print(f"See log: {LOG_PATH}")
        sys.exit(1)

if __name__ == "__main__":
    main()