"""Where everything lives. The project folder is the one above app/.

    app/        the Python program (this folder)
    templates/  page templates and the hop chart script
    config/     files you edit: exclude.txt, families.txt, hop_flavours_extra.json
    hopdata/    hop aroma data from HopDatabase
    data/       your downloaded brews, settings and admin password (private, created on first run)
    output/     the built pages (created on first run)
    .env        your Brewfather User ID and API key (private)
"""
import os

APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
TEMPLATES = os.path.join(ROOT, "templates")
ICONS = os.path.join(TEMPLATES, "icons")
CONFIG = os.path.join(ROOT, "config")
HOPDATA = os.path.join(ROOT, "hopdata")
DATA = os.path.join(ROOT, "data")
OUTPUT = os.path.join(ROOT, "output")
ENV = os.path.join(ROOT, ".env")
BATCHES = os.path.join(DATA, "batches.json")


def template(name):
    return os.path.join(TEMPLATES, name)


def config(name):
    return os.path.join(CONFIG, name)


def app(name):
    return os.path.join(APP, name)
