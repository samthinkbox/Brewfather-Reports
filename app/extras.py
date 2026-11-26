"""Optional local add-ons, kept out of the shared app.

If app/local_extras.py exists (it is gitignored and never packaged), it can add to this installation:

    PAGES = [("mypage.html", "My page")]                 # extra links in the page header, before Import recipe
    JOBS = {"name": ("Button label", ["arg", ...], "Confirm text or empty", needs_brewfather_key)}
                                                          # extra Admin buttons, run like the built-in jobs
    def build(): ...                                      # called after the reports are built (run.py)

Nothing else in the app knows what the add-ons are.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import local_extras as _X  # noqa: E402
except ImportError:
    _X = None


def pages():
    return [tuple(p) for p in getattr(_X, "PAGES", [])]


def jobs():
    """name -> (label, command, confirm text, needs the Brewfather key)."""
    return {k: (v[0], list(v[1]), v[2] if len(v) > 2 else "", bool(v[3]) if len(v) > 3 else True)
            for k, v in getattr(_X, "JOBS", {}).items()}


def build():
    if _X is not None and hasattr(_X, "build"):
        _X.build()
