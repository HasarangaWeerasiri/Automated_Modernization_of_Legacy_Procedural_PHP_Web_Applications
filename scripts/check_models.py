"""Import generated models.py files and run SQLAlchemy configure_mappers() on each.

Usage: python scripts/check_models.py <output_dir>/<app>/models.py [...]
"""

import importlib.util, sys
from sqlalchemy.orm import configure_mappers, clear_mappers
for path in sys.argv[1:]:
    spec = importlib.util.spec_from_file_location("m", path); m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m); configure_mappers()
        mapped = len(m.Base.registry.mappers); tables = len(m.Base.metadata.tables)
        print(f"{path.split('/')[-2]}: configure_mappers OK ({mapped} mapped classes, {tables} tables)")
    except Exception as e:
        print(f"{path.split('/')[-2]}: FAIL {type(e).__name__}: {str(e)[:120]}")
    finally:
        clear_mappers()
