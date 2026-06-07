import logging
import re
from typing import Dict, Any, List, Optional
import httpx
from backend.config import settings

logger = logging.getLogger("backend.github_service")

class GitHubServiceClient:
    def __init__(self):
        self.pat = settings.github_pat
        self.headers = {
            "Authorization": f"Bearer {self.pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.base_url = "https://api.github.com"

    async def get_workflow_jobs(self, owner: str, repo: str, run_id: int) -> List[Dict[str, Any]]:
        """Retrieves the list of jobs for a workflow run and filters for failed ones."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        logger.info(f"Fetching GitHub workflow jobs from: {url}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=20.0)
            if response.status_code != 200:
                logger.error(f"Failed to fetch workflow jobs: {response.status_code} - {response.text}")
                response.raise_for_status()
                
            jobs_data = response.json()
            jobs = jobs_data.get("jobs", [])
            
            failed_jobs = []
            for job in jobs:
                if job.get("conclusion") == "failure":
                    failed_jobs.append(job)
            return failed_jobs

    async def get_job_logs(self, owner: str, repo: str, job_id: int) -> str:
        """Retrieves raw log file contents for a specific job ID (handles 302 redirects)."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        logger.info(f"Fetching GitHub job logs from: {url}")
        
        # httpx needs follow_redirects=True since GitHub logs endpoint returns a redirect to a temporary blob URL
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, headers=self.headers, timeout=30.0)
            if response.status_code != 200:
                logger.error(f"Failed to fetch job log {job_id}: {response.status_code} - {response.text}")
                response.raise_for_status()
            return response.text

    def clean_and_truncate_log(self, raw_log: str, max_chars: int = 35000) -> str:
        """
        Cleans and truncates logs to stay within OpenAI context window.
        Strips timestamps and extracts lines around key error keywords.
        """
        if not raw_log:
            return "No logs found."

        lines = raw_log.splitlines()
        cleaned_lines = []
        
        # 1. Strip timestamps (GitHub logs start with ISO timestamps like "2026-06-07T14:30:52.1234567Z ")
        timestamp_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*")
        for line in lines:
            cleaned_line = timestamp_pattern.sub("", line)
            cleaned_lines.append(cleaned_line)
            
        # 2. Extract error regions
        error_lines = []
        error_keywords = [
            r"\[error\]", r"error:", r"failed:", r"fatal:", r"exception:", 
            r"fail:", r"stderr:", r"exit code", r"failed with exit code",
            r"npm err!", r"pip install.*error", r"traceback", r"caused by",
            r"Error: Process completed with exit code"
        ]
        keyword_regex = re.compile("|".join(error_keywords), re.IGNORECASE)
        
        for idx, line in enumerate(cleaned_lines):
            if keyword_regex.search(line):
                # Grab a little surrounding context (-2 lines and +2 lines)
                start_ctx = max(0, idx - 2)
                end_ctx = min(len(cleaned_lines), idx + 3)
                context_block = f"--- Context (Lines {start_ctx+1}-{end_ctx}) ---\n"
                context_block += "\n".join(cleaned_lines[start_ctx:end_ctx])
                error_lines.append(context_block)
                
        # If we found error segments, compile them
        if error_lines:
            compiled_errors = "\n\n".join(error_lines[:30])
            if len(compiled_errors) <= max_chars:
                return f"[EXTRACTED LOG SEGMENTS FOCUSING ON ERRORS]\n\n{compiled_errors}"

        # 3. Fallback: Take the tail of the log
        char_count = 0
        tail_lines = []
        for line in reversed(cleaned_lines):
            if char_count + len(line) + 1 > max_chars:
                break
            tail_lines.append(line)
            char_count += len(line) + 1
            
        tail_lines.reverse()
        return f"[LOG TAIL (LAST {len(tail_lines)} LINES)]\n\n" + "\n".join(tail_lines)

    async def fetch_failed_log_data(self, owner: str, repo: str, run_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves job list and extracts raw/cleaned logs for the failed job.
        """
        try:
            failed_jobs = await self.get_workflow_jobs(owner, repo, run_id)
            if not failed_jobs:
                logger.warning(f"No failed jobs found for workflow run {run_id}.")
                return None
                
            # Grab the first failed job to analyze
            primary_failure = failed_jobs[0]
            job_name = primary_failure.get("name", "Unknown Job")
            job_id = primary_failure.get("id")
            
            if not job_id:
                logger.warning(f"Failed job '{job_name}' is missing a job ID.")
                return None
                
            raw_log = await self.get_job_logs(owner, repo, job_id)
            cleaned_log = self.clean_and_truncate_log(raw_log)
            
            return {
                "failed_task_name": job_name,
                "raw_log_content": raw_log[:10000],  # Store a small sample in DB
                "cleaned_log_content": cleaned_log
            }
        except Exception as e:
            logger.error(f"Error retrieving failed logs for run {run_id}: {e}")
            return None

# Singleton GitHub Client
github_client = GitHubServiceClient()
