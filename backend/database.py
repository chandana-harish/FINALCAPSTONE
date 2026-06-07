import logging
from datetime import datetime
import uuid
from typing import List, Dict, Any, Optional

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from backend.config import settings

logger = logging.getLogger("backend.database")

class CosmosDBClient:
    def __init__(self):
        self.client: Optional[CosmosClient] = None
        self.database = None
        self.container = None

    def initialize(self):
        """Initializes the Cosmos DB connection and creates DB/container if missing."""
        try:
            logger.info("Initializing Cosmos DB client...")
            self.client = CosmosClient(settings.cosmos_uri, credential=settings.cosmos_key)
            
            # Create database if not exists
            self.database = self.client.create_database_if_not_exists(id=settings.cosmos_database)
            logger.info(f"Database '{settings.cosmos_database}' verified/created.")
            
            # Create container if not exists with partition key /project_name
            self.container = self.database.create_container_if_not_exists(
                id=settings.cosmos_container,
                partition_key=PartitionKey(path="/project_name")
            )
            logger.info(f"Container '{settings.cosmos_container}' verified/created with partition key '/project_name'.")
        except Exception as e:
            logger.error(f"Failed to connect to Cosmos DB: {e}")
            raise e

    def save_analysis(self, analysis_data: Dict[str, Any]) -> Dict[str, Any]:
        """Saves a failure analysis document to Cosmos DB."""
        if not self.container:
            self.initialize()
            
        doc = {
            "id": str(uuid.uuid4()) if "id" not in analysis_data else analysis_data["id"],
            "created_at": datetime.utcnow().isoformat() + "Z",
            **analysis_data
        }
        
        try:
            # upsert_item writes or updates the item in Cosmos DB
            self.container.upsert_item(body=doc)
            logger.info(f"Saved analysis record {doc['id']} for run {doc.get('run_id')} to Cosmos DB.")
            return doc
        except exceptions.CosmosHttpResponseError as e:
            logger.error(f"Cosmos DB insert error: {e}")
            raise e

    def get_analyses(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Queries the latest failure analysis documents."""
        if not self.container:
            self.initialize()
            
        query = "SELECT * FROM c ORDER BY c.created_at DESC"
        try:
            items = list(self.container.query_items(
                query=query,
                enable_cross_partition_query=True,
                max_item_count=limit
            ))
            return items
        except exceptions.CosmosHttpResponseError as e:
            logger.error(f"Cosmos DB query error: {e}")
            return []

    def get_analysis_by_id(self, analysis_id: str, project_name: str) -> Optional[Dict[str, Any]]:
        """Reads a specific analysis record using the ID and partition key (project_name)."""
        if not self.container:
            self.initialize()
            
        try:
            item = self.container.read_item(item=analysis_id, partition_key=project_name)
            return item
        except exceptions.CosmosResourceNotFoundError:
            logger.warning(f"Analysis record {analysis_id} not found in project {project_name}.")
            return None
        except exceptions.CosmosHttpResponseError as e:
            logger.error(f"Cosmos DB read error: {e}")
            return None

    def get_analytics_summary(self) -> Dict[str, Any]:
        """Aggregates failure metrics for dashboard analytics."""
        if not self.container:
            self.initialize()
            
        try:
            items = self.get_analyses(limit=1000)
            
            total_failures = len(items)
            if total_failures == 0:
                return {
                    "total_failures": 0,
                    "avg_severity": 0.0,
                    "classification_counts": {},
                    "severity_distribution": {}
                }
                
            severities = [item.get("severity_score", 0) for item in items if "severity_score" in item]
            avg_severity = sum(severities) / len(severities) if severities else 0.0
            
            classifications = {}
            for item in items:
                c_type = item.get("failure_classification", "unknown").lower()
                classifications[c_type] = classifications.get(c_type, 0) + 1
                
            severity_dist = {str(i): 0 for i in range(1, 11)}
            for sev in severities:
                sev_key = str(int(sev))
                if sev_key in severity_dist:
                    severity_dist[sev_key] += 1

            return {
                "total_failures": total_failures,
                "avg_severity": round(avg_severity, 1),
                "classification_counts": classifications,
                "severity_distribution": severity_dist
            }
        except Exception as e:
            logger.error(f"Error compiling analytics summary: {e}")
            return {
                "total_failures": 0,
                "avg_severity": 0.0,
                "classification_counts": {},
                "severity_distribution": {}
            }

# Singleton DB Client instance
db_client = CosmosDBClient()
