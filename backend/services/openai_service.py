import json
import logging
from typing import Dict, Any
from openai import OpenAI
from backend.config import settings

logger = logging.getLogger("backend.openai_service")

class OpenAIAnalyzer:
    def __init__(self):
        self.api_key = settings.openai_api_key
        self.model_name = settings.openai_model_name
        
        # Instantiate standard OpenAI client
        self.client = OpenAI(
            api_key=self.api_key
        )

    async def analyze_failure(self, pipeline_name: str, failed_task: str, cleaned_logs: str) -> Dict[str, Any]:
        """
        Sends the truncated logs and pipeline context to OpenAI for diagnosis.
        Returns a structured dictionary of results.
        """
        logger.info(f"Initiating OpenAI analysis using model '{self.model_name}'")
        
        system_prompt = """You are an expert DevSecOps and SRE engineer who specializes in diagnosing CI/CD pipeline failures.
Your task is to analyze raw build/test/docker/deployment logs and produce a structured, high-confidence root cause diagnosis and fix suggestions.

You MUST respond ONLY with a valid JSON object. Do not include markdown codeblocks (like ```json) or any conversational text around the JSON.
The JSON object must contain the following fields exactly:
{
  "root_cause": "A concise description of the exact failure cause. Explain which file, line, command, or dependency caused the issue.",
  "fix_suggestion": "Actionable, step-by-step instructions or commands to resolve the issue. Be concrete, do not use vague phrases.",
  "failure_classification": "Must be exactly one of: 'build', 'test', 'docker', 'deployment', or 'other'.",
  "severity_score": 7, // An integer score from 1 (low) to 10 (critical blocker).
  "confidence_score": 0.85 // A float confidence score from 0.0 to 1.0 indicating your certainty.
}
"""

        user_content = f"""
Pipeline Name: {pipeline_name}
Failed Task Name: {failed_task}

--- CLEANED FAILURE LOGS ---
{cleaned_logs}
--- END OF LOGS ---

Analyze the logs above and output the diagnosis JSON object.
"""

        try:
            # We call the model
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.2,
                max_tokens=1500
            )
            
            response_text = response.choices[0].message.content.strip()
            logger.debug(f"Raw OpenAI response: {response_text}")
            
            # Parse the JSON string
            analysis_result = json.loads(response_text)
            
            # Validate classification field
            valid_classes = ["build", "test", "docker", "deployment", "other"]
            classification = analysis_result.get("failure_classification", "other").lower()
            if classification not in valid_classes:
                analysis_result["failure_classification"] = "other"
                
            # Validate severity_score
            severity = analysis_result.get("severity_score", 5)
            try:
                analysis_result["severity_score"] = max(1, min(10, int(severity)))
            except (ValueError, TypeError):
                analysis_result["severity_score"] = 5
                
            # Validate confidence_score
            confidence = analysis_result.get("confidence_score", 0.5)
            try:
                analysis_result["confidence_score"] = max(0.0, min(1.0, float(confidence)))
            except (ValueError, TypeError):
                analysis_result["confidence_score"] = 0.5
                
            return analysis_result
            
        except json.JSONDecodeError as jde:
            logger.error(f"Failed to parse JSON response from OpenAI: {jde}. Raw content: {response_text}")
            return self._get_fallback_analysis(failed_task, "Failed to parse OpenAI JSON output.")
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return self._get_fallback_analysis(failed_task, f"OpenAI API call failed: {str(e)}")

    def _get_fallback_analysis(self, failed_task: str, error_details: str) -> Dict[str, Any]:
        """Provides a safe default response if OpenAI fails."""
        return {
            "root_cause": f"The pipeline task '{failed_task}' failed. Diagnostic engine encountered an error: {error_details}",
            "fix_suggestion": "Please review the raw logs in Azure DevOps manually. Check network settings and OpenAI API key status.",
            "failure_classification": "other",
            "severity_score": 5,
            "confidence_score": 0.0
        }

# Singleton OpenAI Analyzer
openai_analyzer = OpenAIAnalyzer()
