"""Views Chat module."""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from services.ai_engine import DevOpsAgent
import logging

logger = logging.getLogger(__name__)


class AIChatView(APIView):
    permission_classes = [IsAuthenticated]  # SECURITY: Require authentication

    def post(self, request):
        message = request.data.get('message')
        if not message:
            return Response({"detail": "Message required"},
                            status=status.HTTP_400_BAD_REQUEST)

        # SECURITY: Input length limit to prevent abuse
        if len(message) > 2000:
            return Response(
                {"detail": "Message too long. Maximum 2000 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        agent = DevOpsAgent()

        # Simple RAG / Tool Routing
        if "analyze" in message.lower() and "repo" in message.lower():
            # In a real app, we'd extract URL via NLP
            # Mocking extracting the last word as URL
            repo_url = message.split()[-1]

            # SECURITY: Log AI interactions for audit
            logger.info(
                f"AI repo analysis requested by user {
                    request.user.id}: {repo_url}")

            analysis = agent.analyze_repo(
                repo_url, ["Dockerfile", "package.json"])
            return Response({
                "text": f"I've analyzed {repo_url}. It looks like a **{analysis.stack_type}** project. "
                f"I recommend deploying on port **{
                    analysis.recommended_port}**. "
                f"Estimated cost: **{analysis.cost_estimate}**."
            })

        # General Chat
        # If API key is present, use LLM. Else simulate.
        if agent.llm:
            try:
                # SECURITY: Log AI interactions
                logger.info(f"AI chat message from user {request.user.id}")
                response = agent.llm.invoke(message)
                return Response({"text": response.content})
            except Exception as e:
                logger.error(f"AI chat error: {e}")
                return Response(
                    {"text": "I'm having trouble connecting to my brain. Let's try manual deployment."},
                    status=500
                )
        else:
            return Response({
                "text": "I am operating in Simulation Mode. Try asking me to 'analyze github.com/user/repo'."
            })
