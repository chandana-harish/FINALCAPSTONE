import base64
import logging
import re
from typing import Dict, Any, List, Optional
import httpx
from backend.config import settings

logger = logging.getLogger("backend.azure_devops")

class AzureDevOpsClient:
    def __init__(self):
        self.org = settings.ado_org_name
        self.pat = settings.ado_pat
        
        # Base64 encode the PAT for Basic Auth (format is :PAT)
        pat_string = f":{self.pat}"
        base64_pat = base64.b64encode(pat_string.encode("utf-8")).decode("utf-8")
        self.headers = {
            "Authorization": f"Basic {base64_pat}",
            "Content-Type": "application/json"
        }
        self.base_url = f"https://dev.azure.com/{self.org}"

    async def get_build_details(self, project: str, build_id: int) -> Dict[str, Any]:
        """Fetches general metadata for a specific build run."""
        url = f"{self.base_url}/{project}/_apis/build/builds/{build_id}?api-version=7.1"
        logger.info(f"Fetching build details from: {url}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=20.0)
            if response.status_code != 200:
                logger.error(f"Failed to fetch build details: {response.status_code} - {response.text}")
                response.raise_for_status()
            return response.json()

    async def get_failed_timeline_records(self, project: str, build_id: int) -> List[Dict[str, Any]]:
        """Retrieves the timeline/tasks of a build and filters for failed ones."""
        url = f"{self.base_url}/{project}/_apis/build/builds/{build_id}/timeline?api-version=7.1"
        logger.info(f"Fetching build timeline from: {url}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=20.0)
            if response.status_code != 200:
                logger.error(f"Failed to fetch timeline: {response.status_code} - {response.text}")
                response.raise_for_status()
                
            timeline_data = response.json()
            records = timeline_data.get("records", [])
            
            # Filter for tasks that failed
            failed_records = []
            for record in records:
                # We check for failed tasks. In ADO, result is 'failed' when the task fails.
                # 'type' is typically 'Task' for individual pipeline steps.
                if record.get("result") == "failed" and record.get("type") == "Task":
                    failed_records.append(record)
                    
            return failed_records

    async def get_log_content(self, project: str, build_id: int, log_id: int) -> str:
        """Retrieves raw log file contents for a specific log ID."""
        url = f"{self.base_url}/{project}/_apis/build/builds/{build_id}/logs/{log_id}?api-version=7.1"
        logger.info(f"Fetching build log from: {url}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=20.0)
            if response.status_code != 200:
                logger.error(f"Failed to fetch log {log_id}: {response.status_code} - {response.text}")
                response.raise_for_status()
            return response.text

    def clean_and_truncate_log(self, raw_log: str, max_chars: int = 35000) -> str:
        """
        Cleans and truncates logs to stay within LLM context window.
        Extracts relevant error patterns or takes the tail of the log if it's too long.
        """
        if not raw_log:
            return "No logs found."

        lines = raw_log.splitlines()
        
        # 1. Clean noise: strip timestamps at the start of logs (e.g., "2026-06-07T14:30:52.1234567Z Line of log")
        cleaned_lines = []
        timestamp_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*")
        for line in lines:
            # Remove timestamp prefix to save characters
            cleaned_line = timestamp_pattern.sub("", line)
            cleaned_lines.append(cleaned_line)
            
        # 2. Extract lines matching typical error signatures (helps focus the LLM)
        error_lines = []
        error_keywords = [
            r"\[error\]", r"error:", r"failed:", r"fatal:", r"exception:", 
            r"fail:", r"stderr:", r"exit code", r"failed with exit code",
            r"npm err!", r"pip install.*error", r"traceback", r"caused by"
        ]
        keyword_regex = re.compile("|".join(error_keywords), re.IGNORECASE)
        
        # Keep track of indices where errors occurred to extract context
        for idx, line in enumerate(cleaned_lines):
            if keyword_regex.search(line):
                # Grab a little surrounding context (-2 lines and +2 lines)
                start_ctx = max(0, idx - 2)
                end_ctx = min(len(cleaned_lines), idx + 3)
                context_block = f"--- Context (Lines {start_ctx+1}-{end_ctx}) ---\n"
                context_block += "\n".join(cleaned_lines[start_ctx:end_ctx])
                error_lines.append(context_block)
                
        # If we found specific error context, compile it
        if error_lines:
            compiled_errors = "\n\n".join(error_lines[:30]) # Limit to 30 contexts max
            if len(compiled_errors) <= max_chars:
                return f"[EXTRACTED LOG SEGMENTS FOCUSING ON ERRORS]\n\n{compiled_errors}"

        # 3. Fallback: If no distinct error patterns match, or if it is a general compile/build crash,
        # get the last N lines (tail) of the log, since errors are typically at the end of the log.
        char_count = 0
        tail_lines = []
        for line in reversed(cleaned_lines):
            if char_count + len(line) + 1 > max_chars:
                break
            tail_lines.append(line)
            char_count += len(line) + 1
            
        tail_lines.reverse()
        return f"[LOG TAIL (LAST {len(tail_lines)} LINES)]\n\n" + "\n".join(tail_lines)

    async def fetch_failed_log_data(self, project: str, build_id: int) -> Optional[Dict[str, Any]]:
        """
        Combines timeline fetching and log retrieval to return a dictionary of:
        - failed_task_name
        - raw_log_content
        - cleaned_log_content
        """
        try:
            failed_records = await self.get_failed_timeline_records(project, build_id)
            if not failed_records:
                logger.warning(f"No failed tasks found in the timeline for build {build_id}.")
                return None
                
            # Usually we analyze the first failed task that stopped the build
            # You can also analyze multiple, but starting with the first primary failure is best.
            primary_failure = failed_records[0]
            task_name = primary_failure.get("name", "Unknown Task")
            log_ref = primary_failure.get("log")
            
            if not log_ref or "id" not in log_ref:
                logger.warning(f"Failed task '{task_name}' does not have a log reference.")
                return {
                    "failed_task_name": task_name,
                    "raw_log_content": "No log file referenced in Azure DevOps.",
                    "cleaned_log_content": "No log file referenced in Azure DevOps."
                }
                
            log_id = log_ref["id"]
            raw_log = await self.get_log_content(project, build_id, log_id)
            cleaned_log = self.clean_and_truncate_log(raw_log)
            
            return {
                "failed_task_name": task_name,
                "raw_log_content": raw_log[:10000],  # Store a small sample in DB
                "cleaned_log_content": cleaned_log
            }
        except Exception as e:
            logger.error(f"Error retrieving failed logs for build {build_id}: {e}")
            return None

# Singleton ADO Client
ado_client = AzureDevOpsClient()
