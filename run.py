"""Fetch the latest data from Brewfather and rebuild the reports.

    python run.py                 # fetch from Brewfather, then build every page
    python run.py --offline       # rebuild from data/batches.json without calling the API
    python run.py --update-hops   # also download the latest hop data from HopDatabase first
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_report  # noqa: E402
import fetch  # noqa: E402
import hop_flavour  # noqa: E402
import settings  # noqa: E402


def main(argv):
    if "--update-hops" in argv:
        hop_flavour.update_from_github()
    if "--offline" not in argv:
        fetch.main()
    build_report.main()
    if settings.load().get("next_brew") and os.path.exists(os.path.join(HERE, "next_brew.py")):
        import next_brew
        next_brew.main([])  # rebuilds next_brew.html; it only sends to Brewfather with: python next_brew.py --upload


if __name__ == "__main__":
    main(sys.argv[1:])
