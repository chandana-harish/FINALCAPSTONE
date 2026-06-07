import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.database import db_client
from backend.services.github_service import github_client
from backend.services.openai_service import openai_analyzer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("backend.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown database connections."""
    logger.info("Starting up AI CI/CD Failure Analyzer Backend...")
    try:
        # Initialize Cosmos DB database and container
        db_client.initialize()
    except Exception as e:
        logger.error(f"Failed to initialize Cosmos DB on startup: {e}")
        logger.warning("Application will proceed, but DB writes will fail until Cosmos is available.")
    yield
    logger.info("Shutting down backend...")

app = FastAPI(
    title=settings.api_title,
    version="1.0.0",
    lifespan=lifespan
)

async def process_failed_run(owner: str, repo: str, pipeline_name: str, run_id: int, run_number: str):
    """
    Background worker task to fetch build details, job logs,
    analyze with OpenAI, and persist results in Cosmos DB.
    """
    project_name = f"{owner}/{repo}"
    logger.info(f"Processing failed run #{run_number} (ID: {run_id}) for repo '{project_name}'")
    
    try:
        # 1. Fetch failed logs from GitHub REST API
        log_data = await github_client.fetch_failed_log_data(owner, repo, run_id)
        if not log_data:
            logger.warning(f"No failure log data could be retrieved for workflow run {run_id}.")
            return
            
        failed_task = log_data["failed_task_name"]
        cleaned_logs = log_data["cleaned_log_content"]
        raw_log_sample = log_data["raw_log_content"]

        # 2. Analyze failure logs using OpenAI Service
        analysis = await openai_analyzer.analyze_failure(
            pipeline_name=pipeline_name,
            failed_task=failed_task,
            cleaned_logs=cleaned_logs
        )

        # 3. Compile full document structure
        analysis_document = {
            "project_name": project_name,
            "pipeline_name": pipeline_name,
            "run_id": str(run_id),
            "run_number": str(run_number),
            "failed_task_name": failed_task,
            "root_cause": analysis["root_cause"],
            "fix_suggestion": analysis["fix_suggestion"],
            "failure_classification": analysis["failure_classification"],
            "severity_score": analysis["severity_score"],
            "confidence_score": analysis["confidence_score"],
            "log_snippet": raw_log_sample, # Store raw log snippet in database
            "status": "analyzed"
        }

        # 4. Save analysis results to Cosmos DB
        db_client.save_analysis(analysis_document)
        logger.info(f"Successfully processed and stored failure report for run {run_id}")

    except Exception as e:
        logger.error(f"Error executing failure analysis for run {run_id}: {e}", exc_info=True)


@app.post("/webhook/cicd-failure", status_code=status.HTTP_202_ACCEPTED)
async def receive_cicd_failure(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook receiver endpoint for GitHub Actions workflow_run events.
    Parses payload to identify failed workflow runs, triggers analysis, and returns immediately.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid JSON payload"
        )
    
    logger.info("Received webhook event from GitHub Actions")
    logger.debug(f"Webhook payload: {payload}")
    
    # Extract GitHub workflow_run event details
    action = payload.get("action")
    workflow_run = payload.get("workflow_run", {})
    repository = payload.get("repository", {})
    
    # Extract fields
    owner = repository.get("owner", {}).get("login")
    repo = repository.get("name")
    run_id = workflow_run.get("id")
    run_number = workflow_run.get("run_number")
    pipeline_name = workflow_run.get("name")
    conclusion = workflow_run.get("conclusion")
    
    # Check if this is a completed workflow_run failure
    if not run_id or not owner or not repo:
        logger.warning(f"Malformed payload. Missing run_id, owner, or repo. Payload: {payload}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "Missing required fields run_id, owner, or repo"}
        )

    # Verify if the action is completed and conclusion is failure
    is_failed = (action == "completed" and conclusion == "failure")
    
    if not is_failed:
        logger.info(f"Workflow run {run_id} in {owner}/{repo} has action '{action}' and conclusion '{conclusion}'. Skipping analysis.")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored", 
                "message": f"Run status is not completed failure (Action: {action}, Conclusion: {conclusion}). No action taken."
            }
        )
        
    if not pipeline_name:
        pipeline_name = f"Workflow-{run_id}"
    if not run_number:
        run_number = str(run_id)

    # Schedule background analysis
    background_tasks.add_task(
        process_failed_run,
        owner=owner,
        repo=repo,
        pipeline_name=pipeline_name,
        run_id=run_id,
        run_number=str(run_number)
    )
    
    return {
        "status": "processing",
        "message": "GitHub workflow run failure analysis triggered in the background.",
        "details": {
            "owner": owner,
            "repo": repo,
            "pipeline": pipeline_name,
            "run_id": run_id,
            "run_number": run_number
        }
    }

@app.get("/health")
def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "service": settings.api_title}
