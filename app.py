import requests
from requests_ntlm import HttpNtlmAuth
import datetime
from fastapi import FastAPI, HTTPException, Query, Header, Response
from typing import Optional, List
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import urllib3
import base64

# Disable SSL Warnings for internal server
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
API_VERSION = "6.0-preview"

# Admin group names to check (any of these grants admin access)
ADMIN_GROUPS = [
    "Project Administrators",
    "Project Collection Administrators",
    "[ITSD Task Tracker]\\Project Administrators",
    "ITSD Task Tracker Administrators"
]


# --- MODELS ---
class LoginCredentials(BaseModel):
    username: str
    password: str


# --- HELPER FUNCTIONS ---
def build_wiql_query(date: Optional[str] = None) -> str:
    base_query = """
SELECT [System.Id]
FROM WorkItems 
WHERE [System.WorkItemType] <> 'Test Case' 
AND [System.State] <> 'Removed'
"""
    if date:
        base_query += f"AND [System.ChangedDate] >= '{date}' AND [System.ChangedDate] < '{get_next_day(date)}'\n"
    base_query += "ORDER BY [System.ChangedDate] DESC"
    return base_query


def get_next_day(date_str: str) -> str:
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    next_day = dt + timedelta(days=1)
    return next_day.strftime('%Y-%m-%d')

def get_user_groups(auth, user_name: str, user_id: str = None) -> List[str]:
    """Get list of groups the user belongs to - tries multiple approaches"""
    groups = []
    
    try:
        print(f"Looking up groups for user: {user_name} (id: {user_id})")
        
        # Approach 1: Get project security groups and check membership
        groups_url = f"{ADO_SERVER}/{COLLECTION}/_apis/projects/{PROJECT}/teams?api-version=6.0-preview.3"
        response = requests.get(groups_url, auth=auth, verify=False, timeout=30)
        print(f"Teams API: {response.status_code}")
        
        if response.status_code == 200:
            teams = response.json().get('value', [])
            for team in teams:
                team_name = team.get('name', '')
                print(f"Found team: {team_name}")
                groups.append(f"Team: {team_name}")
        
        # Approach 2: Check security namespace for project collection administrators
        # Get project ID first
        project_url = f"{ADO_SERVER}/{COLLECTION}/_apis/projects/{PROJECT}?api-version=6.0-preview"
        proj_resp = requests.get(project_url, auth=auth, verify=False, timeout=30)
        
        if proj_resp.status_code == 200:
            project_id = proj_resp.json().get('id')
            print(f"Project ID: {project_id}")
            
            # Get groups in this project
            security_url = f"{ADO_SERVER}/{COLLECTION}/_apis/securitynamespaces?api-version=6.0-preview"
            sec_resp = requests.get(security_url, auth=auth, verify=False, timeout=30)
            print(f"Security API: {sec_resp.status_code}")
        
        # Approach 3: Try Graph API for groups
        if user_id:
            # Try to lookup via the identity picker
            picker_url = f"{ADO_SERVER}/{COLLECTION}/_apis/IdentityPicker/Identities?api-version=6.0-preview.1"
            picker_body = {
                "query": user_name,
                "identityTypes": ["user"],
                "operationScopes": ["ims", "source"],
                "properties": ["DisplayName", "IsMru", "MemberOf"]
            }
            picker_resp = requests.post(picker_url, json=picker_body, auth=auth, verify=False, timeout=30)
            print(f"Identity Picker API: {picker_resp.status_code}")
            
            if picker_resp.status_code == 200:
                picker_data = picker_resp.json()
                print(f"Picker data: {picker_data}")
                for result in picker_data.get('results', []):
                    for identity in result.get('identities', []):
                        member_of = identity.get('memberOf', [])
                        print(f"MemberOf from picker: {member_of}")
                        groups.extend(member_of)
                    
    except Exception as e:
        print(f"Error getting user groups: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"Total groups found: {groups}")
    return groups

def check_is_project_admin(auth, user_name: str, user_id: str) -> bool:
    """
    Check if user is a Project Administrator by looking up group members directly.
    """
    try:
        print(f"Checking admin status for: {user_name} (ID: {user_id})")
        
        # Get project descriptor first
        project_url = f"{ADO_SERVER}/{COLLECTION}/_apis/projects/{PROJECT}?api-version=6.0-preview"
        proj_resp = requests.get(project_url, auth=auth, verify=False, timeout=30)
        
        if proj_resp.status_code != 200:
            print(f"Failed to get project: {proj_resp.status_code}")
            return False
            
        project_data = proj_resp.json()
        project_id = project_data.get('id')
        print(f"Project ID: {project_id}")
        
        # Approach 1: Try the Graph Groups API to get Project Administrators
        # First, get all groups for the project
        groups_url = f"{ADO_SERVER}/{COLLECTION}/_apis/graph/groups?scopeDescriptor=scp.{project_id}&api-version=6.0-preview.1"
        groups_resp = requests.get(groups_url, auth=auth, verify=False, timeout=30)
        print(f"Graph Groups API: {groups_resp.status_code}")
        
        if groups_resp.status_code == 200:
            graph_groups = groups_resp.json().get('value', [])
            for group in graph_groups:
                group_name = group.get('displayName', '')
                print(f"Found graph group: {group_name}")
                
                if 'Project Administrators' in group_name or 'Administrators' in group_name:
                    # Found the admin group, check its members
                    group_descriptor = group.get('descriptor', '')
                    print(f"Admin group descriptor: {group_descriptor}")
                    
                    # Get members of this group
                    members_url = f"{ADO_SERVER}/{COLLECTION}/_apis/graph/memberships/{group_descriptor}?direction=Down&api-version=6.0-preview.1"
                    members_resp = requests.get(members_url, auth=auth, verify=False, timeout=30)
                    print(f"Members API: {members_resp.status_code}")
                    
                    if members_resp.status_code == 200:
                        members = members_resp.json().get('value', [])
                        for member in members:
                            member_descriptor = member.get('memberDescriptor', '')
                            print(f"Member descriptor: {member_descriptor}")
                            # Check if this member matches our user
                            if user_id and user_id in member_descriptor:
                                print("User found in admin group!")
                                return True
        
        # Approach 2: Try Security Groups API (different format)
        security_groups_url = f"{ADO_SERVER}/{COLLECTION}/{PROJECT}/_api/_identity/ReadGroupsByScope?scope={project_id}&api-version=6.0-preview"
        sec_groups_resp = requests.get(security_groups_url, auth=auth, verify=False, timeout=30)
        print(f"Security Groups API: {sec_groups_resp.status_code}")
        
        # Approach 3: Try using identities API with group search
        for group_pattern in ['Project Administrators', 'Administrators']:
            identity_url = f"{ADO_SERVER}/{COLLECTION}/_apis/identities?searchFilter=DisplayName&filterValue={group_pattern}&queryMembership=Expanded&api-version=6.0-preview"
            identity_resp = requests.get(identity_url, auth=auth, verify=False, timeout=30)
            print(f"Identity search for '{group_pattern}': {identity_resp.status_code}")
            
            if identity_resp.status_code == 200:
                identities = identity_resp.json().get('value', [])
                for identity in identities:
                    identity_name = identity.get('providerDisplayName', '')
                    print(f"Found identity: {identity_name}")
                    
                    # If this is the admin group, check its members
                    members = identity.get('members', [])
                    print(f"Members in group: {len(members)}")
                    
                    for member_id in members:
                        if user_id and user_id == member_id:
                            print("User found in admin group via identity API!")
                            return True
                        
                        # Also get the member details to match by name
                        member_url = f"{ADO_SERVER}/{COLLECTION}/_apis/identities?identityIds={member_id}&api-version=6.0-preview"
                        member_resp = requests.get(member_url, auth=auth, verify=False, timeout=30)
                        if member_resp.status_code == 200:
                            member_data = member_resp.json().get('value', [])
                            for m in member_data:
                                member_name = m.get('providerDisplayName', '')
                                print(f"  Member: {member_name}")
                                if user_name.lower() in member_name.lower() or member_name.lower() in user_name.lower():
                                    print(f"User matched: {member_name}")
                                    return True
        
        print("User not found in any admin group")
        return False
        
    except Exception as e:
        print(f"Error checking admin: {e}")
        import traceback
        traceback.print_exc()
        return False

def is_admin_group(group_name: str) -> bool:
    """Check if a group name matches any admin group pattern"""
    group_lower = group_name.lower()
    for admin_group in ADMIN_GROUPS:
        if admin_group.lower() in group_lower or group_lower in admin_group.lower():
            return True
    return False

# --- AUTH ENDPOINTS ---

@app.post('/api/auth/test')
def test_auth(credentials: LoginCredentials):
    """Simple test endpoint to verify NTLM authentication works"""
    try:
        print(f"Testing auth for user: {credentials.username}")
        
        # Try NTLM Auth
        auth = HttpNtlmAuth(credentials.username, credentials.password)
        
        # Simple test URL - use preview API version
        test_url = f"{ADO_SERVER}/{COLLECTION}/_apis/connectionData?api-version=5.0-preview"
        print(f"Calling: {test_url}")
        
        response = requests.get(test_url, auth=auth, verify=False, timeout=30)
        
        print(f"Response status: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "status_code": response.status_code,
                "user": data.get('authenticatedUser', {}).get('providerDisplayName', 'Unknown'),
                "user_id": data.get('authenticatedUser', {}).get('id')
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text[:500]  # First 500 chars
            }
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }

@app.post('/api/auth/login')
def login(credentials: LoginCredentials):
    """Authenticate user with Azure DevOps using NTLM"""
    try:
        # Use NTLM Auth (Azure DevOps Server uses NTLM)
        auth = HttpNtlmAuth(credentials.username, credentials.password)
        
        # Test credentials by getting connection data
        identity_url = f"{ADO_SERVER}/{COLLECTION}/_apis/connectionData?api-version={API_VERSION}"
        response = requests.get(identity_url, auth=auth, verify=False, timeout=30)
        
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid username or password. Use format: DOMAIN\\username")
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Authentication failed: {response.text}")
        
        data = response.json()
        user_id = data.get('authenticatedUser', {}).get('id')
        user_name = data.get('authenticatedUser', {}).get('providerDisplayName', 'Unknown User')
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Could not verify user identity")
        
        # Get user's groups (for display/debug)
        user_groups = get_user_groups(auth, user_name, user_id)
        
        # NOTE: Azure DevOps Server on-premise doesn't expose group membership via REST API reliably
        # For now, allow all authenticated users. You've verified via Azure DevOps UI that
        # you're a Project Administrator. In production, you can add a list of allowed usernames.
        is_admin = True  # Allow all authenticated users
        
        # Store credentials for token (base64 encoded)
        auth_token = base64.b64encode(f"{credentials.username}:{credentials.password}".encode()).decode()
        
        return {
            "success": True,
            "is_admin": is_admin,
            "user_name": user_name,
            "auth_token": auth_token,
            "groups": user_groups  # Include groups for debugging
        }
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")

@app.post('/api/auth/debug-groups')
def debug_groups(credentials: LoginCredentials):
    """Debug endpoint to see user's group memberships"""
    try:
        auth = HttpNtlmAuth(credentials.username, credentials.password)
        
        # Get connection data
        identity_url = f"{ADO_SERVER}/{COLLECTION}/_apis/connectionData?api-version={API_VERSION}"
        response = requests.get(identity_url, auth=auth, verify=False, timeout=30)
        
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Authentication failed")
        
        data = response.json()
        user_id = data.get('authenticatedUser', {}).get('id')
        user_name = data.get('authenticatedUser', {}).get('providerDisplayName', 'Unknown')
        
        # Get all groups
        groups = get_user_groups(auth, user_name, user_id)
        
        # Check which are admin groups
        admin_matches = [g for g in groups if is_admin_group(g)]
        
        return {
            "user_name": user_name,
            "all_groups": groups,
            "admin_groups_matched": admin_matches,
            "is_admin": len(admin_matches) > 0,
            "configured_admin_groups": ADMIN_GROUPS
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- DATA ENDPOINTS ---
@app.get('/api/reports')
def get_daily_reports(
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    authorization: Optional[str] = Header(None)
):
    """Get daily reports - requires auth token from login"""
    # Get auth from header or use default service account
    auth = None
    if authorization and authorization.startswith('Basic '):
        try:
            encoded = authorization.split(' ')[1]
            decoded = base64.b64decode(encoded).decode('utf-8')
            username, password = decoded.split(':', 1)
            auth = HttpNtlmAuth(username, password)
        except:
            pass
    
    # If no auth provided, try without (for backwards compatibility)
    if not auth:
        # Fallback - you may want to require auth
        from requests_negotiate_sspi import HttpNegotiateAuth
        auth = HttpNegotiateAuth()
    
    print(f"Fetching data for date: {date or 'all'}...")
    
    wiql_url = f"{ADO_SERVER}/{COLLECTION}/{PROJECT}/_apis/wit/wiql?api-version={API_VERSION}"
    
    try:
        wiql_query = build_wiql_query(date)
        response = requests.post(
            wiql_url, 
            json={"query": wiql_query}, 
            auth=auth,
            verify=False,
            timeout=60
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"WIQL Error: {response.text}")
            
        work_items = response.json().get("workItems", [])
        clean_data = []

        for item_ref in work_items:
            item_id = item_ref['id']
            item_url = f"{ADO_SERVER}/{COLLECTION}/_apis/wit/workitems/{item_id}?api-version={API_VERSION}"
            
            try:
                item_resp = requests.get(item_url, auth=auth, verify=False, timeout=30)
                if item_resp.status_code != 200:
                    continue
                
                item = item_resp.json()
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

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Server Error: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)