"""Fetch the latest data from Brewfather and rebuild the reports.

    python app/run.py                 # fetch from Brewfather, then build every page
    python app/run.py --offline       # rebuild from data/batches.json without calling the API
    python app/run.py --update-hops   # also download the latest hop data from HopDatabase first
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_report  # noqa: E402
import extras  # noqa: E402
import fetch  # noqa: E402
import hop_flavour  # noqa: E402


def main(argv):
    if "--update-hops" in argv:
        hop_flavour.update_from_github()
    if "--offline" not in argv:
        fetch.main()
    build_report.main()
    extras.build()  # optional local add-ons (extras.py)


if __name__ == "__main__":
    main(sys.argv[1:])
