"""Download all batches (with their full recipes) to data/batches.json."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brewfather import Brewfather  # noqa: E402
import paths  # noqa: E402

def main():
    bf = Brewfather()
    batches = bf.list_all("/batches", complete="True")
    os.makedirs(paths.DATA, exist_ok=True)
    path = paths.BATCHES
    with open(path, "w", encoding="utf-8") as f:
        json.dump(batches, f, indent=1)
    print(f"Saved {len(batches)} batches to {path}")

if __name__ == "__main__":
    main()
