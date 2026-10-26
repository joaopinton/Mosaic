"""Mosaic 2: gestore di foto e video completamente locale."""

import os

__version__ = "2.2.1"  # uguale a `version` in pyproject.toml (lo verifica tests/test_version.py)

# Con 8 GB di RAM le librerie numeriche non devono aprire un thread (e un buffer) per ogni core: metà dei core bastano.
# Va fatto prima che numpy venga importato, quindi qui, nel primo modulo del pacchetto.
_THREADS = str(max(1, (os.cpu_count() or 2) // 2))
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, _THREADS)
