"""Node capacity routing — selects best node for new rooms/calls."""
import logging

logger = logging.getLogger(__name__)


class MediaCapacityService:
    """Selects the best media node for a new room or call."""

    @staticmethod
    def find_best_node(room_type: str = "video"):
        """Return node with highest capacity_score.

        Args:
            room_type: 'voice' or 'video'.

        Returns:
            MediaNodeProfile or None if no nodes available.
        """
        from ..models import MediaNodeProfile

        return (
            MediaNodeProfile.objects
            .filter(
                server__agent_ready=True,
                server__provision_status="DONE",
                server__status="ONLINE",
            )
            .order_by("-capacity_score")
            .first()
        )

    @staticmethod
    def calculate_score(node) -> float:
        """Calculate capacity score for a node. 0.0 = fully loaded, 1.0 = empty.

        Media load weighs more than CPU — a node at capacity on voice/video
        should route elsewhere even if CPU is idle (media is buffer-constrained).
        """
        voice_load = node.active_calls / max(node.max_voice_calls, 1)
        video_load = node.active_participants / max(node.max_participants, 1)
        rtp_load = node.active_rtp_sessions / max(node.max_rtp_sessions, 1)
        cpu_load = node.cpu_percent / 100

        # Weighted aggregate — media load weighs more than CPU
        score = 1.0 - max(
            voice_load * 0.3,
            video_load * 0.5,
            rtp_load * 0.1,
            cpu_load * 0.1,
        )
        return round(max(0.0, min(1.0, score)), 4)

    @staticmethod
    def get_global_capacity() -> dict:
        """Aggregate capacity across all active nodes."""
        from ..models import MediaNodeProfile

        nodes = MediaNodeProfile.objects.filter(
            server__agent_ready=True,
            server__provision_status="DONE",
        )

        total_calls = sum(n.active_calls for n in nodes)
        total_rooms = sum(n.active_rooms for n in nodes)
        total_participants = sum(n.active_participants for n in nodes)
        total_rtp = sum(n.active_rtp_sessions for n in nodes)
        avg_score = (
            sum(n.capacity_score for n in nodes) / max(len(nodes), 1)
        )

        return {
            "node_count": nodes.count(),
            "total_active_calls": total_calls,
            "total_active_rooms": total_rooms,
            "total_active_participants": total_participants,
            "total_rtp_sessions": total_rtp,
            "average_capacity_score": round(avg_score, 4),
        }
