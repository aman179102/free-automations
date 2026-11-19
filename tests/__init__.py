"""Test suite initialization and environment bootstrap for free-automations."""

import glob
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

# Automatically detect and link .venv site-packages if running with system python
venv_sites = glob.glob(str(WORKSPACE_ROOT / ".venv" / "lib" / "python*" / "site-packages"))
for site in venv_sites:
    if site not in sys.path:
        sys.path.insert(0, site)
