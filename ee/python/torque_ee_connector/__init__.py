"""Enterprise-only Torque cloud connector package boundary.

The community daemon can optionally import this package through
``torque.cloud_hooks`` when explicitly enabled, but community packaging excludes
``ee/`` so this module is absent from the open build.
"""

from .connector import ConnectorConfig, EnterpriseConnector, create_connector

__all__ = ["ConnectorConfig", "EnterpriseConnector", "create_connector"]
__version__ = "0.1.0"
