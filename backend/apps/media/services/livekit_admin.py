"""LiveKit Server API client — one instance per media node."""
import logging

logger = logging.getLogger(__name__)


class LiveKitAdminService:
    """Client for LiveKit Server API. Operates via management daemon proxy."""

    def __init__(self, node):
        """
        Args:
            node: MediaNodeProfile instance.
        """
        self.node = node
        self.base_url = f"http://{node.server.wg_address}:{node.livekit_port}"
        self.api_key = node.livekit_api_key
        self.api_secret = node.livekit_api_secret

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def create_room(self, name: str, max_participants: int = 100) -> dict:
        """Create a new LiveKit room."""
        # TODO: Implement via management daemon or direct API
        logger.info("Creating LiveKit room %s on node %s", name, self.node.server_id)
        return {"name": name, "sid": "", "max_participants": max_participants}

    def delete_room(self, name: str) -> None:
        """Delete a LiveKit room."""
        logger.info("Deleting LiveKit room %s on node %s", name, self.node.server_id)

    def list_rooms(self) -> list[dict]:
        """List all active LiveKit rooms."""
        # TODO: Implement via management daemon
        return []

    def get_room(self, name: str) -> dict:
        """Get room details."""
        return {"name": name}

    def list_participants(self, room: str) -> list[dict]:
        """List participants in a room."""
        return []

    def remove_participant(self, room: str, participant: str) -> None:
        """Remove a participant from a room."""
        logger.info("Removing participant %s from room %s", participant, room)

    def start_egress(self, room: str) -> dict:
        """Start recording via LiveKit Egress."""
        logger.info("Starting egress for room %s", room)
        return {"egress_id": ""}

    def stop_egress(self, egress_id: str) -> None:
        """Stop a LiveKit Egress."""
        logger.info("Stopping egress %s", egress_id)
