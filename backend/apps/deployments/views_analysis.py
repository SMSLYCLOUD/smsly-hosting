from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
import time
import re

class RepoAnalysisView(APIView):
    permission_classes = [IsAuthenticated]  # SECURITY: Require authentication
    
    def post(self, request):
        repo_url = request.data.get('repo_url')
        if not repo_url:
            return Response({"detail": "Repo URL required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # SECURITY: Validate repo URL format to prevent SSRF
        if not re.match(r'^https?://(github\.com|gitlab\.com|bitbucket\.org)/', repo_url):
            return Response(
                {"detail": "Only GitHub, GitLab, and Bitbucket repositories are supported."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Simulation of analysis
        # Real impl would clone repo and look for files
        time.sleep(1) # Simulate network delay

        stack = "unknown"
        if "django" in repo_url.lower():
            stack = "django"
        elif "node" in repo_url.lower() or "express" in repo_url.lower():
            stack = "node"

        return Response({
            "stack": stack,
            "detected_files": ["Dockerfile", "requirements.txt"] if stack == "django" else ["package.json"],
            "suggested_port": 8000 if stack == "django" else 3000,
            "recommended_addons": ["POSTGRES"] if stack == "django" else ["REDIS"]
        })
