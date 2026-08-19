"""HTTP route groups for the Secure Gateway."""

from knoa_platform.gateway.routes.artifacts import ArtifactRoutes
from knoa_platform.gateway.routes.configuration import ConfigurationRoutes
from knoa_platform.gateway.routes.console import ConsoleRoutes
from knoa_platform.gateway.routes.conversations import ConversationRoutes
from knoa_platform.gateway.routes.device import DeviceRoutes
from knoa_platform.gateway.routes.extensions import ExtensionRoutes
from knoa_platform.gateway.routes.fleet import FleetRoutes
from knoa_platform.gateway.routes.remote_resources import RemoteResourceRoutes
from knoa_platform.gateway.routes.secrets import SecretRoutes
from knoa_platform.gateway.routes.tasks import TaskRoutes

__all__ = [
    "ArtifactRoutes",
    "ConfigurationRoutes",
    "ConsoleRoutes",
    "ConversationRoutes",
    "DeviceRoutes",
    "ExtensionRoutes",
    "FleetRoutes",
    "RemoteResourceRoutes",
    "SecretRoutes",
    "TaskRoutes",
]
