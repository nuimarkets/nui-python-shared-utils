"""Build-time checks for Python AWS Lambda deployment ZIPs.

Kept free of imports: ``runtime`` is loaded inside Lambda images that have only the
standard library, so importing this package must not pull in the host-side modules.
"""

__version__ = "0.1.0"
