"""Taking money: the provider adapters, the books each event writes, and the service that ties them together.

``providers/`` is the only place that knows which payment provider is connected. Everything else
speaks the plain types in ``types.py``, so choosing or changing a provider touches one file.
"""
