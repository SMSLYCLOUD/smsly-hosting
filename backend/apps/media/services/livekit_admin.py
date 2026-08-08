"""LiveKit Server API client — one instance per media node.

Routes through the management daemon proxy on the node's WireGuard
address.  No IPs are hardcoded — the node's host/port come from
``MediaNodeProfile`` which is populated by self-registration.
"""
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_LIVEKIT_TIMEOUT = 10


class LiveKitAdminService:
    """Client for LiveKit Server API on a media node.

    All requests go through the OpenResty reverse proxy on the node,
    which routes ``/livekit/*`` to the LiveKit API (default port 7880).
    """

    def __init__(self, node):
        """
        Args:
            node: MediaNodeProfile instance.
        """
        self.node = node
        host = node.livekit_host or node.server.wg_address or node.server.host
        port = node.livekit_port or 7880
        self.base_url = f"http://{host}:{port}"
        self.api_key = node.livekit_api_key
        self.api_secret = node.livekit_api_secret

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict | None = None) -> dict:
        """POST to the LiveKit API. Returns the JSON response or empty dict."""
        url = f"{self.base_url}{path}"
        try:
            resp = requests.post(
                url,
                json=payload or {},
                headers=self._headers(),
                timeout=_LIVEKIT_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("LiveKit API %s failed: %s", path, exc)
            return {}

    def _get(self, path: str) -> dict | list:
        """GET from the LiveKit API."""
        url = f"{self.base_url}{path}"
        try:
            resp = requests.get(
                url,
                headers=self._headers(),
                timeout=_LIVEKIT_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("LiveKit API %s failed: %s", path, exc)
            return {}

    def _delete(self, path: str) -> bool:
        """DELETE on the LiveKit API. Returns True on success."""
        url = f"{self.base_url}{path}"
        try:
            resp = requests.delete(
                url,
                headers=self._headers(),
                timeout=_LIVEKIT_TIMEOUT,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.warning("LiveKit API %s failed: %s", path, exc)
            return False

    # -- Rooms --

    def create_room(self, name: str, max_participants: int = 100) -> dict:
        """Create a new LiveKit room."""
        result = self._post("/twirp/livekit.RoomService/CreateRoom", {
            "name": name,
            "max_participants": max_participants,
        })
        if result:
            logger.info("Created LiveKit room %s on node %s", name, self.node.server_id)
        return result

    def delete_room(self, name: str) -> bool:
        """Delete a LiveKit room."""
        ok = self._post("/twirp/livekit.RoomService/DeleteRoom", {
            "room": name,
        })
        if ok:
            logger.info("Deleted LiveKit room %s on node %s", name, self.node.server_id)
        return bool(ok)

    def list_rooms(self) -> list[dict]:
        """List all active LiveKit rooms."""
        result = self._post("/twirp/livekit.RoomService/ListRooms", {})
        return result.get("rooms", []) if isinstance(result, dict) else []

    def get_room(self, name: str) -> dict:
        """Get room details."""
        result = self._post("/twirp/livekit.RoomService/ListRooms", {})
        rooms = result.get("rooms", []) if isinstance(result, dict) else []
        for room in rooms:
            if room.get("name") == name:
                return room
        return {}

    # -- Participants --

    def list_participants(self, room: str) -> list[dict]:
        """List participants in a room."""
        result = self._post("/twirp/livekit.RoomService/ListParticipants", {
            "room": room,
        })
        return result.get("participants", []) if isinstance(result, dict) else []

    def remove_participant(self, room: str, participant: str) -> bool:
        """Remove a participant from a room."""
        ok = self._post("/twirp/livekit.RoomService/RemoveParticipant", {
            "room": room,
            "identity": participant,
        })
        if ok:
            logger.info("Removed participant %s from room %s", participant, room)
        return bool(ok)

    # -- Egress (recording) --

    def start_egress(self, room: str, file_output: dict | None = None) -> dict:
        """Start recording via LiveKit Egress."""
        payload = {
            "room_composite": {
                "room_name": room,
                "layout": "speaker",
                "file_outputs": [file_output or {
                    "file_type": "FILE_TYPE_MP4",
                    "filepath": f"recordings/{room}_{int(time.time())}.mp4",
                }],
            },
        }
        result = self._post("/twirp/livekit.Egress/StartRecordedRoomComposite", payload)
        if result:
            logger.info("Started egress for room %s: %s", room, result.get("egress_id"))
        return result

    def stop_egress(self, egress_id: str) -> bool:
        """Stop a LiveKit Egress."""
        ok = self._post("/twirp/livekit.Egress/StopEgress", {
            "egress_id": egress_id,
        })
        if ok:
            logger.info("Stopped egress %s", egress_id)
        return bool(ok)
