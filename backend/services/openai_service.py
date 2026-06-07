import json
import logging
from typing import Dict, Any
import httpx
from openai import OpenAI
from backend.config import settings

logger = logging.getLogger("backend.openai_service")

class OpenAIAnalyzer:
    def __init__(self):
        self.openai_key = settings.openai_api_key
        self.openai_model = settings.openai_model_name
        self.gemini_key = settings.gemini_api_key
        self.gemini_model = settings.gemini_model_name

        # If gemini_key is not set but openai_key looks like a Gemini key (starts with AIzaSy)
        if not self.gemini_key and self.openai_key and self.openai_key.startswith("AIzaSy"):
            self.gemini_key = self.openai_key

        # Instantiate standard OpenAI client only if key is present and not a Gemini key
        if self.openai_key and not self.openai_key.startswith("AIzaSy"):
            self.openai_client = OpenAI(api_key=self.openai_key)
        else:
            self.openai_client = None

    async def analyze_failure(self, pipeline_name: str, failed_task: str, cleaned_logs: str) -> Dict[str, Any]:
        """
        Sends the truncated logs and pipeline context to Google Gemini (or OpenAI fallback) for diagnosis.
        Returns a structured dictionary of results.
        """
        # Determine if we should use Gemini
        if self.gemini_key:
            return await self._analyze_with_gemini(pipeline_name, failed_task, cleaned_logs, self.gemini_key)
        else:
            return await self._analyze_with_openai(pipeline_name, failed_task, cleaned_logs)

    async def _analyze_with_gemini(self, pipeline_name: str, failed_task: str, cleaned_logs: str, api_key: str) -> Dict[str, Any]:
        model = self.gemini_model or "gemini-2.5-flash"
        logger.info(f"Initiating Google Gemini analysis using model '{model}'")
        
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

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": user_content}
                    ]
                }
            ],
            "systemInstruction": {
                "parts": [
                    {"text": system_prompt}
                ]
            },
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2
            }
        }
        
        response_text = ""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30.0)
                if response.status_code != 200:
                    logger.error(f"Gemini API returned error code {response.status_code}: {response.text}")
                    return self._get_fallback_analysis(failed_task, f"Gemini API returned status code {response.status_code}")
                
                response_data = response.json()
                candidates = response_data.get("candidates", [])
                if not candidates:
                    return self._get_fallback_analysis(failed_task, "Gemini API returned no candidates.")
                
                response_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                logger.debug(f"Raw Gemini response: {response_text}")
                
                analysis_result = json.loads(response_text)
                return self._validate_and_sanitize_result(analysis_result)
                
        except json.JSONDecodeError as jde:
            logger.error(f"Failed to parse JSON response from Gemini: {jde}. Raw content: {response_text}")
            return self._get_fallback_analysis(failed_task, "Failed to parse Gemini JSON output.")
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return self._get_fallback_analysis(failed_task, f"Gemini API call failed: {str(e)}")

    async def _analyze_with_openai(self, pipeline_name: str, failed_task: str, cleaned_logs: str) -> Dict[str, Any]:
        logger.info(f"Initiating OpenAI analysis using model '{self.openai_model}'")
        
        if not self.openai_client:
            return self._get_fallback_analysis(failed_task, "OpenAI API key is missing or not configured.")

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

        response_text = ""
        try:
            response = self.openai_client.chat.completions.create(
                model=self.openai_model,
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
            
            analysis_result = json.loads(response_text)
            return self._validate_and_sanitize_result(analysis_result)
            
        except json.JSONDecodeError as jde:
            logger.error(f"Failed to parse JSON response from OpenAI: {jde}. Raw content: {response_text}")
            return self._get_fallback_analysis(failed_task, "Failed to parse OpenAI JSON output.")
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return self._get_fallback_analysis(failed_task, f"OpenAI API call failed: {str(e)}")

    def _validate_and_sanitize_result(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validates and sanitizes the fields of the LLM response."""
        valid_classes = ["build", "test", "docker", "deployment", "other"]
        classification = analysis_result.get("failure_classification", "other").lower()
        if classification not in valid_classes:
            analysis_result["failure_classification"] = "other"
            
        severity = analysis_result.get("severity_score", 5)
        try:
            analysis_result["severity_score"] = max(1, min(10, int(severity)))
        except (ValueError, TypeError):
            analysis_result["severity_score"] = 5
            
        confidence = analysis_result.get("confidence_score", 0.5)
        try:
            analysis_result["confidence_score"] = max(0.0, min(1.0, float(confidence)))
        except (ValueError, TypeError):
            analysis_result["confidence_score"] = 0.5
            
        return analysis_result

    def _get_fallback_analysis(self, failed_task: str, error_details: str) -> Dict[str, Any]:
        """Provides a safe default response if the LLM call fails."""
        return {
            "root_cause": f"The pipeline task '{failed_task}' failed. Diagnostic engine encountered an error: {error_details}",
            "fix_suggestion": "Please review the raw logs in GitHub Actions manually. Check network settings and API key status.",
            "failure_classification": "other",
            "severity_score": 5,
            "confidence_score": 0.0
        }

# Singleton OpenAI Analyzer (also acts as Gemini Analyzer)
openai_analyzer = OpenAIAnalyzer()
