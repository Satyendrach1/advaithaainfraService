from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, Depends, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import secrets
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
import asyncio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Create uploads directory in frontend
BASE_DIR = Path(__file__).resolve().parent.parent  # project root
UPLOAD_DIR = BASE_DIR / "frontend" / "public" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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

# Zoho SMTP Configuration
ZOHO_SMTP_HOST = os.environ.get('ZOHO_SMTP_HOST', 'smtp.zoho.in')
ZOHO_SMTP_PORT = int(os.environ.get('ZOHO_SMTP_PORT', '465'))
ZOHO_EMAIL = os.environ.get('ZOHO_EMAIL', '')
ZOHO_PASSWORD = os.environ.get('ZOHO_PASSWORD', '')
RECIPIENT_EMAIL = os.environ.get('RECIPIENT_EMAIL', 'info@advaithainfra.com')

# ==================== #
# Models
# ==================== #
class MediaItem(BaseModel):
    url: str
    type: str = "image"  # "image" or "video"
    caption: Optional[str] = None

class ProjectBase(BaseModel):
    title: str
    description: str
    category: str
    sub_category: str
    location: str
    image_url: str  # Main/thumbnail image
    gallery: List[dict] = []  # Array of {url, type, caption}
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
    gallery: Optional[List[dict]] = None
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

class EnquiryRequest(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    project: Optional[str] = None
    message: Optional[str] = None
    form_type: Optional[str] = "general"  # general, project, investment

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
# Email Functions
# ==================== #
def send_email_sync(to_email: str, subject: str, html_content: str):
    """Synchronous email sending using Zoho SMTP"""
    if not ZOHO_EMAIL or not ZOHO_PASSWORD:
        logger.error("Zoho SMTP credentials not configured")
        raise Exception("Email service not configured")
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header("Advaithaa Infra", 'utf-8')), ZOHO_EMAIL))
    msg['To'] = to_email
    
    # Attach HTML content
    html_part = MIMEText(html_content, 'html', 'utf-8')
    msg.attach(html_part)
    
    # Connect and send via SSL
    server = smtplib.SMTP_SSL(ZOHO_SMTP_HOST, ZOHO_SMTP_PORT)
    server.login(ZOHO_EMAIL, ZOHO_PASSWORD)
    server.sendmail(ZOHO_EMAIL, [to_email], msg.as_string())
    server.quit()
    
    logger.info(f"Email sent successfully to {to_email}")

async def send_email_async(to_email: str, subject: str, html_content: str):
    """Non-blocking email sending"""
    await asyncio.to_thread(send_email_sync, to_email, subject, html_content)

def create_enquiry_email_html(enquiry: EnquiryRequest) -> str:
    """Create HTML email template for enquiry"""
    form_type_label = {
        "general": "General Enquiry",
        "project": "Project Enquiry",
        "investment": "Investment/JV Enquiry"
    }.get(enquiry.form_type, "Website Enquiry")
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin:0;padding:0;font-family:Arial,sans-serif;background-color:#f5f5f5;">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background-color:#ffffff;">
            <tr>
                <td style="background-color:#0A0A0A;padding:24px;text-align:center;">
                    <h1 style="color:#ECC16A;margin:0;font-size:24px;">Advaithaa Infra</h1>
                </td>
            </tr>
            <tr>
                <td style="padding:32px;">
                    <h2 style="color:#0A0A0A;margin:0 0 24px 0;font-size:20px;border-bottom:2px solid #ECC16A;padding-bottom:12px;">
                        New {form_type_label}
                    </h2>
                    <table width="100%" cellpadding="8" cellspacing="0" style="font-size:14px;">
                        <tr>
                            <td style="color:#666;width:120px;vertical-align:top;"><strong>Name:</strong></td>
                            <td style="color:#0A0A0A;">{enquiry.name}</td>
                        </tr>
                        <tr>
                            <td style="color:#666;vertical-align:top;"><strong>Phone:</strong></td>
                            <td style="color:#0A0A0A;">
                                <a href="tel:{enquiry.phone}" style="color:#0A0A0A;text-decoration:none;">{enquiry.phone}</a>
                            </td>
                        </tr>
                        <tr>
                            <td style="color:#666;vertical-align:top;"><strong>Email:</strong></td>
                            <td style="color:#0A0A0A;">
                                <a href="mailto:{enquiry.email or 'Not provided'}" style="color:#0A0A0A;text-decoration:none;">{enquiry.email or 'Not provided'}</a>
                            </td>
                        </tr>
                        <tr>
                            <td style="color:#666;vertical-align:top;"><strong>Project Interest:</strong></td>
                            <td style="color:#0A0A0A;">{enquiry.project or 'General Enquiry'}</td>
                        </tr>
                        <tr>
                            <td style="color:#666;vertical-align:top;"><strong>Message:</strong></td>
                            <td style="color:#0A0A0A;">{enquiry.message or 'No message provided'}</td>
                        </tr>
                    </table>
                    <div style="margin-top:24px;padding:16px;background-color:#f8f8f8;border-left:4px solid #ECC16A;">
                        <p style="margin:0;font-size:12px;color:#666;">
                            <strong>Submitted:</strong> {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p UTC')}
                        </p>
                    </div>
                </td>
            </tr>
            <tr>
                <td style="background-color:#0A0A0A;padding:16px;text-align:center;">
                    <p style="color:#666;margin:0;font-size:12px;">
                        This enquiry was submitted from the Advaithaa Infra website.
                    </p>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

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
# Enquiry Routes (Public)
# ==================== #
@api_router.post("/enquiry")
async def submit_enquiry(enquiry: EnquiryRequest, background_tasks: BackgroundTasks):
    """Submit an enquiry form and send email notification"""
    try:
        # Store enquiry in database
        enquiry_doc = {
            "id": str(uuid.uuid4())[:8],
            "name": enquiry.name,
            "phone": enquiry.phone,
            "email": enquiry.email,
            "project": enquiry.project,
            "message": enquiry.message,
            "form_type": enquiry.form_type,
            "status": "new",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.enquiries.insert_one(enquiry_doc)
        
        # Send email notification in background
        form_type_label = {
            "general": "General Enquiry",
            "project": "Project Enquiry", 
            "investment": "Investment/JV Enquiry"
        }.get(enquiry.form_type, "Website Enquiry")
        
        subject = f"New {form_type_label} from {enquiry.name}"
        html_content = create_enquiry_email_html(enquiry)
        
        background_tasks.add_task(send_email_async, RECIPIENT_EMAIL, subject, html_content)
        
        return {
            "success": True,
            "message": "Enquiry submitted successfully. We will contact you soon!"
        }
    except Exception as e:
        logger.error(f"Error submitting enquiry: {str(e)}")
        # Still return success if email fails - enquiry is saved
        return {
            "success": True,
            "message": "Enquiry submitted. We will contact you soon!"
        }

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
    
    # Allow images and videos
    allowed_types = ['image/', 'video/']
    if not any(file.content_type.startswith(t) for t in allowed_types):
        raise HTTPException(status_code=400, detail="File must be an image or video")
    
    file_extension = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
    file_id = str(uuid.uuid4())[:12]
    filename = f"{file_id}.{file_extension}"
    file_path = UPLOAD_DIR / filename
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Determine media type
    media_type = 'video' if file.content_type.startswith('video/') else 'image'
    
    # Return URL pointing to frontend static folder
    return {"success": True, "url": f"/uploads/{filename}", "filename": filename, "type": media_type}

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
