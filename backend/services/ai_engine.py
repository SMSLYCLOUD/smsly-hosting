"""AI Engine service."""
# pylint:
# disable=line-too-long,broad-exception-caught,logging-fstring-interpolation,too-few-public-methods,wrong-import-order
import logging
import os
import statistics
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --- Structured Output Models ---


class StackAnalysis(BaseModel):
    """Stack analysis model."""
    stack_type: str = Field(
        description="The detected tech stack (e.g., 'django', 'nextjs', 'node')")
    recommended_port: int = Field(
        description="The internal port the app likely listens on")
    required_addons: list[str] = Field(
        description="List of addons needed (e.g., ['POSTGRES', 'REDIS'])")
    build_strategy: str = Field(
        description="Recommended build strategy (e.g., 'dockerfile', 'buildpacks')")
    cost_estimate: str = Field(description="Estimated monthly cost string")

# --- AI Engine ---


class DevOpsAgent:
    """DevOps Agent."""

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if self.api_key:
            self.llm = ChatGoogleGenerativeAI(
                model="gemini-pro",
                google_api_key=self.api_key,
                temperature=0.2)
        else:
            self.llm = None
            logger.warning(
                "GEMINI_API_KEY not found. Running in Simulation Mode.")

    def analyze_repo(self, repo_url: str,
                     file_list: list[str]) -> StackAnalysis:
        """
        Analyzes a repository structure to determine stack and requirements.
        """
        if not self.llm:
            return self._simulate_analysis(repo_url)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an elite DevOps AI agent. Analyze the provided repository file list.
            Detect the framework, database requirements, and build configuration.
            Return JSON matching the schema."""),
            ("user", "Repo: {repo_url}\nFiles: {file_list}")
        ])

        chain = prompt | self.llm.with_structured_output(StackAnalysis)
        try:
            return chain.invoke(
                {"repo_url": repo_url, "file_list": ", ".join(file_list)})
        except Exception as e:
            logger.error(f"AI Analysis Failed: {e}")
            return self._simulate_analysis(repo_url)

    def diagnose_logs(self, build_logs: str) -> str:
        """
        Diagnoses build/runtime failures.
        """
        if not self.llm:
            return self._simulate_diagnosis(build_logs)

        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert DevOps engineer. Analyze these build logs and explain the failure in simple terms. Suggest a concrete fix."),
            ("user", "{logs}")
        ])

        try:
            response = self.llm.invoke(prompt.format_messages(
                logs=build_logs[-2000:]))  # Context window check
            return response.content
        except Exception as e:
            logger.error(f"AI Diagnosis Failed: {e}")
            return "AI Analysis failed. Please check logs manually."

    def detect_anomalies(self, metrics: list[float], metric_name: str) -> dict[str, Any]:
        """
        Detects anomalies in a time-series of metrics using Statistical Z-Score.
        This provides a 'Custom ML' baseline without needing heavy deps like scikit-learn.
        """
        if len(metrics) < 10:
            return {"status": "insufficient_data", "anomalies": []}

        try:
            mean = statistics.mean(metrics)
            stdev = statistics.stdev(metrics)

            if stdev == 0:
                return {"status": "stable", "anomalies": []}

            anomalies = []
            threshold = 3.0  # 3 Sigma rule

            for i, val in enumerate(metrics):
                z_score = (val - mean) / stdev
                if abs(z_score) > threshold:
                    anomalies.append({
                        "index": i,
                        "value": val,
                        "z_score": round(z_score, 2),
                        "severity": "CRITICAL" if abs(z_score) > 4 else "WARNING"
                    })

            if anomalies:
                # If anomalies found, use LLM to explain context (if available)
                explanation = "Statistical anomaly detected."
                if self.llm:
                    try:
                        prompt = f"Metric '{metric_name}' showed values {metrics}. Anomalies found at {anomalies}. Explain potential cause in one sentence."
                        explanation = self.llm.invoke(prompt).content
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        logger.exception("LLM anomaly explanation failed: %s", exc)

                return {
                    "status": "anomaly_detected",
                    "count": len(anomalies),
                    "anomalies": anomalies,
                    "explanation": explanation
                }

            return {"status": "normal", "anomalies": []}

        except Exception as e:
            logger.error(f"Anomaly Detection Failed: {e}")
            return {"status": "error", "message": str(e)}

    def _simulate_analysis(self, repo_url):
        # Fallback heuristic logic
        if "django" in repo_url.lower():
            return StackAnalysis(
                stack_type="django",
                recommended_port=8000,
                required_addons=["POSTGRES"],
                build_strategy="dockerfile",
                cost_estimate="~$15/mo"
            )
        return StackAnalysis(
            stack_type="node",
            recommended_port=3000,
            required_addons=["REDIS"],
            build_strategy="nixpacks",
            cost_estimate="~$10/mo"
        )

    def _simulate_diagnosis(self, logs):
        if "requirements.txt" in logs:
            return "Missing requirements.txt file."
        return "Generic build failure."
