import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.database import db_client
from backend.services.azure_devops import ado_client
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

async def process_failed_run(project_name: str, pipeline_name: str, build_id: int, run_number: str):
    """
    Background worker task to fetch build details, timeline logs,
    analyze with Azure OpenAI, and persist results in Cosmos DB.
    """
    logger.info(f"Processing failed run #{run_number} (ID: {build_id}) for project '{project_name}'")
    
    try:
        # 1. Fetch failed logs from Azure DevOps REST API
        log_data = await ado_client.fetch_failed_log_data(project_name, build_id)
        if not log_data:
            logger.warning(f"No failure log data could be retrieved for build {build_id}.")
            return
            
        failed_task = log_data["failed_task_name"]
        cleaned_logs = log_data["cleaned_log_content"]
        raw_log_sample = log_data["raw_log_content"]

        # 2. Analyze failure logs using Azure OpenAI Service
        analysis = await openai_analyzer.analyze_failure(
            pipeline_name=pipeline_name,
            failed_task=failed_task,
            cleaned_logs=cleaned_logs
        )

        # 3. Compile full document structure
        analysis_document = {
            "project_name": project_name,
            "pipeline_name": pipeline_name,
            "run_id": str(build_id),
            "run_number": run_number,
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
        logger.info(f"Successfully processed and stored failure report for build {build_id}")

    except Exception as e:
        logger.error(f"Error executing failure analysis for build {build_id}: {e}", exc_info=True)


@app.post("/webhook/cicd-failure", status_code=status.HTTP_202_ACCEPTED)
async def receive_cicd_failure(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook receiver endpoint for Azure DevOps Service Hooks.
    Parses payload to identify failed builds/runs, triggers analysis, and returns immediately.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid JSON payload"
        )
    
    logger.info("Received service hook webhook event from Azure DevOps")
    logger.debug(f"Webhook payload: {payload}")
    
    # 1. Extract event details (Handles both Build Completed and Run State Changed schemas)
    event_type = payload.get("eventType")
    resource = payload.get("resource", {})
    
    project_name = None
    pipeline_name = None
    build_id = None
    run_number = None
    is_failed = False
    
    # Schema A: ms.vss-pipelines.run-state-changed-event (YAML pipelines)
    if "run" in resource:
        run_data = resource.get("run", {})
        pipeline_data = resource.get("pipeline", {})
        project_data = resource.get("project", {})
        
        project_name = project_data.get("name")
        pipeline_name = pipeline_data.get("name")
        build_id = run_data.get("id")
        run_number = run_data.get("name") # YAML run name contains run number
        
        # State can be 'completed', result can be 'failed'
        state = run_data.get("state")
        result = run_data.get("result")
        is_failed = (state == "completed" and result == "failed")
        
    # Schema B: build.complete (Classic build pipelines)
    else:
        project_data = resource.get("project", {})
        definition_data = resource.get("definition", {})
        
        project_name = project_data.get("name")
        pipeline_name = definition_data.get("name")
        build_id = resource.get("id")
        run_number = resource.get("buildNumber")
        
        result = resource.get("result") or resource.get("status")
        is_failed = (result == "failed")
        
    # Fallback to containers object if project is nested differently
    if not project_name:
        containers = payload.get("resourceContainers", {})
        project_name = containers.get("project", {}).get("id") or "unknown-project"
        
    # Validate payload minimum requirements
    if not build_id or not project_name:
        logger.warning(f"Malformed payload. Missing buildId or project name. Payload: {payload}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "Missing required fields buildId or project_name"}
        )

    # 2. Skip analysis if the pipeline did not fail
    if not is_failed:
        logger.info(f"Build {build_id} in project '{project_name}' has status '{result or 'succeeded'}'. Skipping AI analysis.")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ignored", 
                "message": f"Run status is not failed (Status: {result or 'succeeded'}). No action taken."
            }
        )
        
    if not pipeline_name:
        pipeline_name = f"Pipeline-{build_id}"
    if not run_number:
        run_number = str(build_id)

    # 3. Schedule asynchronous background processing to prevent webhook timeouts
    background_tasks.add_task(
        process_failed_run,
        project_name=project_name,
        pipeline_name=pipeline_name,
        build_id=build_id,
        run_number=run_number
    )
    
    return {
        "status": "processing",
        "message": "Failure analysis triggered in the background.",
        "details": {
            "project": project_name,
            "pipeline": pipeline_name,
            "run_id": build_id,
            "run_number": run_number
        }
    }

@app.get("/health")
def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "service": settings.api_title}
