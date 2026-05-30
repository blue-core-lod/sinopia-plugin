import os
import pathlib

PLUGIN_DIR         = pathlib.Path(__file__).parent.parent.parent
BLUECORE_URL       = os.environ.get("BLUECORE_URL", "https://dev.bcld.info").rstrip("/")
ENVIRONMENT        = os.environ.get("ENVIRONMENT", "")
SINOPIA_VERSION    = os.environ.get("SINOPIA_VERSION", "4.0.0")
BF_INTEROP_VERSION = os.environ.get("BF_INTEROP_VERSION", "v0.3.0")

_BCLD_HEADERS = {"User-Agent": "Sinopia Editor"}
_BF_VOCAB     = "http://id.loc.gov/ontologies/bibframe/"
PAGE_SIZE      = 10
