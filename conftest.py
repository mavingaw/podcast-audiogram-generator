"""Make the backend importable when pytest runs from the repository root.

The tests import `app.*`, which only resolves when `backend/` is on the path.
Running `pytest` from inside `backend/` gets that for free; running
`pytest backend/tests` from the root — which is the obvious thing to type, and
what a CI step or an IDE will do — did not, and failed at collection with
"No module named 'app'". This puts the two invocations on equal footing.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).parent / "backend"
if BACKEND.is_dir() and str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
