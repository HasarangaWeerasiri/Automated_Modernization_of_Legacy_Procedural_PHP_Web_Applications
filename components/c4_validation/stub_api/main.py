"""Hand-written stand-in for the migrated system.

It mimics the API that C1-C3 are expected to produce, so the full differential loop
can be proven before their outputs exist. It has no endpoints yet: they are added
from build step 6 onwards, one per legacy endpoint under validation.
"""

from fastapi import FastAPI

app = FastAPI(title="C4 stub API")
