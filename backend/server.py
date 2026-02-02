from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import secrets
import hashlib

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Create uploads directory
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBasic()

# Admin credentials (in production, store hashed in DB)
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'advaithaa2024')

# Session tokens storage (in production, use Redis)
active_sessions = {}

# ==================== #
# Models
# ==================== #
class ProjectBase(BaseModel):
    title: str
    description: str
    category: str
    sub_category: str
    location: str
    image_url: str
    features: List[str] = []
    highlights: dict = {}
    is_featured: bool = False

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    location: Optional[str] = None
    image_url: Optional[str] = None
    features: Optional[List[str]] = None
    highlights: Optional[dict] = None
    is_featured: Optional[bool] = None

class JobBase(BaseModel):
    title: str
    department: str
    location: str
    type: str
    description: str
    requirements: List[str] = []
    is_active: bool = True

class JobCreate(JobBase):
    pass

class JobUpdate(BaseModel):
    title: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    requirements: Optional[List[str]] = None
    is_active: Optional[bool] = None

class AdminLogin(BaseModel):
    username: str
    password: str

# ==================== #
# Auth Functions
# ==================== #
def verify_token(token: str):
    if token in active_sessions:
        session = active_sessions[token]
        # Check if session is still valid (24 hours)
        if datetime.now(timezone.utc).timestamp() - session['created'] < 86400:
            return True
    return False

def create_session(username: str):
    token = secrets.token_urlsafe(32)
    active_sessions[token] = {
        'username': username,
        'created': datetime.now(timezone.utc).timestamp()
    }
    return token

# ==================== #
# Admin Auth Routes
# ==================== #
@api_router.post("/admin/login")
async def admin_login(credentials: AdminLogin):
    if credentials.username == ADMIN_USERNAME and credentials.password == ADMIN_PASSWORD:
        token = create_session(credentials.username)
        return {"success": True, "token": token, "message": "Login successful"}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@api_router.post("/admin/logout")
async def admin_logout(token: str):
    if token in active_sessions:
        del active_sessions[token]
    return {"success": True, "message": "Logged out"}

@api_router.get("/admin/verify")
async def verify_session(token: str):
    if verify_token(token):
        return {"valid": True}
    raise HTTPException(status_code=401, detail="Invalid or expired session")

# ==================== #
# Project Routes (Public)
# ==================== #
@api_router.get("/projects")
async def get_projects(category: Optional[str] = None):
    query = {}
    if category:
        query["category"] = category
    projects = await db.projects.find(query, {"_id": 0}).to_list(100)
    return projects

@api_router.get("/projects/{project_id}")
async def get_project(project_id: str):
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

# ==================== #
# Project Routes (Admin)
# ==================== #
@api_router.post("/admin/projects")
async def create_project(project: ProjectCreate, token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    project_id = str(uuid.uuid4())[:8]
    doc = {
        "id": project_id,
        **project.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.projects.insert_one(doc)
    return {"success": True, "id": project_id, "message": "Project created"}

@api_router.put("/admin/projects/{project_id}")
async def update_project(project_id: str, project: ProjectUpdate, token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    update_data = {k: v for k, v in project.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    result = await db.projects.update_one(
        {"id": project_id},
        {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return {"success": True, "message": "Project updated"}

@api_router.delete("/admin/projects/{project_id}")
async def delete_project(project_id: str, token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await db.projects.delete_one({"id": project_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return {"success": True, "message": "Project deleted"}

# ==================== #
# Job Routes (Public)
# ==================== #
@api_router.get("/jobs")
async def get_jobs(active_only: bool = True):
    query = {"is_active": True} if active_only else {}
    jobs = await db.jobs.find(query, {"_id": 0}).to_list(100)
    return jobs

@api_router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

# ==================== #
# Job Routes (Admin)
# ==================== #
@api_router.post("/admin/jobs")
async def create_job(job: JobCreate, token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    job_id = str(uuid.uuid4())[:8]
    doc = {
        "id": job_id,
        **job.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.jobs.insert_one(doc)
    return {"success": True, "id": job_id, "message": "Job created"}

@api_router.put("/admin/jobs/{job_id}")
async def update_job(job_id: str, job: JobUpdate, token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    update_data = {k: v for k, v in job.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    result = await db.jobs.update_one(
        {"id": job_id},
        {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {"success": True, "message": "Job updated"}

@api_router.delete("/admin/jobs/{job_id}")
async def delete_job(job_id: str, token: str):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await db.jobs.delete_one({"id": job_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {"success": True, "message": "Job deleted"}

# ==================== #
# Image Upload
# ==================== #
@api_router.post("/admin/upload")
async def upload_image(file: UploadFile = File(...), token: str = ""):
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    file_extension = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
    file_id = str(uuid.uuid4())[:12]
    filename = f"{file_id}.{file_extension}"
    file_path = UPLOAD_DIR / filename
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    return {"success": True, "url": f"/api/uploads/{filename}", "filename": filename}

# Serve uploaded files
from fastapi.responses import FileResponse

@api_router.get("/uploads/{filename}")
async def get_upload(filename: str):
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)

# ==================== #
# Seed Data
# ==================== #
@api_router.post("/seed-data")
async def seed_data():
    existing = await db.projects.count_documents({})
    if existing > 0:
        return {"message": "Data already seeded"}
    
    projects = [
        {
            "id": "alkapuri",
            "title": "Advaithaa Alkapuri Residences",
            "description": "Meticulously crafted apartment project designed to elevate modern urban living, epitomizing luxury, comfort, and convenience.",
            "category": "residential",
            "sub_category": "Apartments",
            "location": "Alkapuri, Nagole, Hyderabad",
            "image_url": "https://images.unsplash.com/photo-1545324418-cc1a3fa10c00?w=800",
            "features": ["55 Premium 3BHK Units", "2,100 sq.ft each flat", "9,000 sqft Clubhouse", "Swimming Pool", "24/7 Security"],
            "highlights": {"area": "4,726 sq.y.", "floors": "5 floors", "units": "55 units"},
            "is_featured": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "id": "narsampalle",
            "title": "Narsampalle Premium Plots",
            "description": "Invest in Growth, Live with Vision. Prime plots with HMDA-approved layouts and strategic connectivity.",
            "category": "plots",
            "sub_category": "Open Plots",
            "location": "Narsampalle Village, Keesara Mandal",
            "image_url": "https://images.unsplash.com/photo-1500382017468-9049fed747ef?w=800",
            "features": ["HMDA-Approved", "40' Wide Roads", "Underground Infrastructure", "10,000 SFT Clubhouse"],
            "highlights": {"approval": "HMDA Approved", "road_width": "40 feet", "plot_range": "180-360 sq.y."},
            "is_featured": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    ]
    
    jobs = [
        {
            "id": "eng001",
            "title": "Project Engineer",
            "department": "Engineering",
            "location": "Hyderabad",
            "type": "Full-time",
            "description": "Oversee construction projects and ensure quality delivery.",
            "requirements": ["B.E/B.Tech in Civil Engineering", "3-5 years experience", "Construction management software knowledge"],
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "id": "sales001",
            "title": "Sales Executive",
            "department": "Sales",
            "location": "Hyderabad",
            "type": "Full-time",
            "description": "Drive property sales and build customer relationships.",
            "requirements": ["Bachelor's degree", "2+ years real estate sales", "Excellent negotiation skills"],
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    ]
    
    await db.projects.insert_many(projects)
    await db.jobs.insert_many(jobs)
    
    return {"message": "Data seeded successfully"}

# Root endpoint
@api_router.get("/")
async def root():
    return {"message": "Advaithaa Infra API"}

# Include the router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
