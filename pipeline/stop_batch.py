"""Stop a running canary batch gracefully: write canary/<batch>/STOP.

The orchestrator stops starting new phases; the step currently running gets one CTRL_BREAK_EVENT, its MACR
child finishes or unwinds and releases its own lease, and the orchestrator waits for the exit. Nothing is
killed. (MACR maintainer guidance, 2026-09-14: after transport starts there is no honest cancel; a
force-killed client leaves a lease that only expiry + operator admission-reconcile can clear.)

Usage: python stop_batch.py <batch>
"""
import sys
from pathlib import Path

from af_common import LAB, utc_now

bdir = LAB / "canary" / sys.argv[1]
(bdir / "STOP").write_text(utc_now() + "\n", encoding="utf-8")
print(f"STOP written to {bdir / 'STOP'}; the batch stops after the current step exits on its own")
