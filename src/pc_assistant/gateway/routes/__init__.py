"""HTTP route groups for the Secure Gateway."""

from pc_assistant.gateway.routes.artifacts import ArtifactRoutes
from pc_assistant.gateway.routes.conversations import ConversationRoutes
from pc_assistant.gateway.routes.device import DeviceRoutes
from pc_assistant.gateway.routes.tasks import TaskRoutes

__all__ = ["ArtifactRoutes", "ConversationRoutes", "DeviceRoutes", "TaskRoutes"]
