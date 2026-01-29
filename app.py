import requests
from requests_negotiate_sspi import HttpNegotiateAuth
import datetime
from fastapi import FastAPI, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import urllib3

# Disable SSL Warnings for internal server matching Daily Reminder app
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURATION ---
ADO_SERVER = "https://rtadevops.rta.global"
COLLECTION = "ITSD"
PROJECT = "ITSD Task Tracker"
API_VERSION = "6.0"

# --- PYDANTIC MODELS ---
class UpdateWorkItem(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None

# --- WIQL QUERY TEMPLATE (REST API) ---
def build_wiql_query(date: Optional[str] = None) -> str:
    base_query = """
SELECT [System.Id]
FROM WorkItems 
WHERE [System.WorkItemType] <> 'Test Case' 
AND [System.State] <> 'Removed'
"""
    if date:
        # Filter by ChangedDate matching the selected date
        base_query += f"AND [System.ChangedDate] >= '{date}T00:00:00Z' AND [System.ChangedDate] < '{date}T23:59:59Z'\n"
    base_query += "ORDER BY [System.ChangedDate] DESC"
    return base_query

@app.get('/api/reports')
def get_daily_reports(date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)")):
    print(f"Fetching data using REST API for date: {date or 'all'}...")
    
    auth = HttpNegotiateAuth()
    wiql_url = f"{ADO_SERVER}/{COLLECTION}/{PROJECT}/_apis/wit/wiql?api-version={API_VERSION}"
    
    try:
        # 1. Execute WIQL to get IDs (Same as monitor_tasks in ref app)
        wiql_query = build_wiql_query(date)
        response = requests.post(
            wiql_url, 
            json={"query": wiql_query}, 
            auth=auth,
            verify=False
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"WIQL Error: {response.text}")
            
        work_items = response.json().get("workItems", [])
        clean_data = []

        # 2. Iterate and Fetch Details Individually (Exactly like Daily Reminder py)
        for item_ref in work_items:
            item_id = item_ref['id']
            item_url = f"{ADO_SERVER}/{COLLECTION}/_apis/wit/workitems/{item_id}?api-version={API_VERSION}"
            
            try:
                item_resp = requests.get(item_url, auth=auth, verify=False)
                if item_resp.status_code != 200:
                    continue
                
                # Parse Item
                item = item_resp.json()
                fields = item.get('fields', {})
                
                # Logic from Reference App adapted for Dashboard Data Model
                
                # Assigned To Logic
                assigned_to = fields.get('System.AssignedTo')
                user_name = "Unassigned"
                if isinstance(assigned_to, dict):
                    user_name = assigned_to.get('displayName', 'Unknown')
                elif isinstance(assigned_to, str):
                    # Handle "Name <Email>" format common in ADO
                    if '<' in assigned_to:
                        user_name = assigned_to.split('<')[0].strip()
                    else:
                        user_name = assigned_to

                # Days Open Calc
                created_date_str = fields.get('System.CreatedDate')
                days_open = 0
                if created_date_str:
                    try:
                        dt = datetime.datetime.fromisoformat(created_date_str.replace('Z', '+00:00'))
                        now = datetime.datetime.now(datetime.timezone.utc)
                        days_open = (now - dt).days
                    except:
                        pass

                clean_item = {
                    "id": item_ref['id'],
                    "title": fields.get('System.Title'),
                    "type": fields.get('System.WorkItemType'),
                    "status": fields.get('System.State'),
                    "assigned_to": user_name,
                    "updated_at": fields.get('System.ChangedDate'),
                    "urgency": fields.get('Custom.Urgency'),
                    "days_open": days_open
                }
                clean_data.append(clean_item)
                
            except Exception as e:
                print(f"Error processing item {item_id}: {e}")
                continue
                
        return clean_data

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")

@app.patch('/api/reports/{item_id}')
def update_work_item(item_id: int, update_data: UpdateWorkItem):
    """Update a work item's state and/or assigned_to in Azure DevOps"""
    print(f"Updating work item {item_id} with {update_data}")
    
    auth = HttpNegotiateAuth()
    update_url = f"{ADO_SERVER}/{COLLECTION}/_apis/wit/workitems/{item_id}?api-version={API_VERSION}"
    
    # Build JSON Patch document for ADO API
    patch_operations = []
    
    if update_data.status:
        patch_operations.append({
            "op": "replace",
            "path": "/fields/System.State",
            "value": update_data.status
        })
    
    if update_data.assigned_to:
        patch_operations.append({
            "op": "replace",
            "path": "/fields/System.AssignedTo",
            "value": update_data.assigned_to
        })
    
    if not patch_operations:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    try:
        response = requests.patch(
            update_url,
            json=patch_operations,
            auth=auth,
            verify=False,
            headers={"Content-Type": "application/json-patch+json"}
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code, 
                detail=f"ADO Update Error: {response.text}"
            )
        
        # Parse and return updated item
        item = response.json()
        fields = item.get('fields', {})
        
        assigned_to = fields.get('System.AssignedTo')
        user_name = "Unassigned"
        if isinstance(assigned_to, dict):
            user_name = assigned_to.get('displayName', 'Unknown')
        elif isinstance(assigned_to, str):
            if '<' in assigned_to:
                user_name = assigned_to.split('<')[0].strip()
            else:
                user_name = assigned_to
        
        return {
            "id": item_id,
            "title": fields.get('System.Title'),
            "type": fields.get('System.WorkItemType'),
            "status": fields.get('System.State'),
            "assigned_to": user_name,
            "updated_at": fields.get('System.ChangedDate'),
            "urgency": fields.get('Custom.Urgency'),
            "days_open": 0  # Not recalculating here
        }
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)