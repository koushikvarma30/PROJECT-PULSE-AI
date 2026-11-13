from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import engine, Base, get_db
from models import Project, Task, Requirement, TeamMember, TeamProject, Bug, Activity, ProjectModule, UserStory, ProjectRisk
import os
import json
from dotenv import load_dotenv
from google import genai
Base.metadata.create_all(bind=engine)

with Session(bind=engine) as db:
    members = db.query(TeamMember).all()

    for member in members:
        if member.project_id is not None:
            existing = db.query(TeamProject).filter(
                TeamProject.team_member_id == member.id,
                TeamProject.project_id == member.project_id
            ).first()

            if not existing:
                db.add(TeamProject(
                    team_member_id=member.id,
                    project_id=member.project_id,
                    active=True
                ))

    db.commit()

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

gemini_client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)
app = FastAPI(title="PROJECT PULSE AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    status: str = "Planning"

class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str
    status: str
    progress: float
    tasks: int
    members: int
    bugs: int
    class Config:
        from_attributes = True

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "Medium"
    status: str = "todo"
    assignee: str = "PM"
    points: int = 1
    project_id: int | None = None

class TaskResponse(BaseModel):
    id: int
    title: str
    description: str
    priority: str
    status: str
    assignee: str
    points: int
    project_id: int | None
    class Config:
        from_attributes = True

def sync_project(db: Session, project_id: int | None):
    if not project_id:
        return
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    project.tasks = len(tasks)
    project.progress = round(sum(1 for t in tasks if t.status == "done") / len(tasks) * 100, 1) if tasks else 0
    db.commit()

@app.get("/")
def home():
    return {"message": "PROJECT PULSE AI Backend is running"}

@app.get("/health")
def health():
    return {"status": "online", "database": "connected"}

@app.get("/projects", response_model=list[ProjectResponse])
def get_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.id.desc()).all()

# ============================================================
# GEMINI AI SPRINT PLANNER
# ============================================================

class SprintPlanRequest(BaseModel):
    project_id: int
    duration: int = 14
    instructions: str = ""


@app.post("/ai/sprints/generate")
def generate_ai_sprint(
    data: SprintPlanRequest,
    db: Session = Depends(get_db)
):

    # Validate sprint duration
    if data.duration not in [7, 14]:
        raise HTTPException(
            status_code=400,
            detail="Sprint duration must be 7 or 14 days."
        )

    # Check Gemini configuration
    if not gemini_client:
        raise HTTPException(
            status_code=503,
            detail="Gemini is not configured. Check GEMINI_API_KEY."
        )

    try:

        # ----------------------------------------------------
        # 1. FETCH SELECTED PROJECT
        # ----------------------------------------------------

        project = db.query(Project).filter(
            Project.id == data.project_id
        ).first()

        if not project:
            raise HTTPException(
                status_code=404,
                detail="Selected project was not found."
            )

        # ----------------------------------------------------
        # 2. FETCH PROJECT TASKS
        # ----------------------------------------------------

        tasks = db.query(Task).filter(
            Task.project_id == data.project_id
        ).all()

        # ----------------------------------------------------
        # 3. FETCH PROJECT BUGS
        # ----------------------------------------------------

        bugs = db.query(Bug).filter(
            Bug.project_id == data.project_id
        ).all()

        # ----------------------------------------------------
        # 4. FETCH PROJECT TEAM MEMBERS
        # ----------------------------------------------------

        memberships = db.query(TeamProject).filter(
            TeamProject.project_id == data.project_id,
            TeamProject.active == True
        ).all()

        member_ids = {
            membership.team_member_id
            for membership in memberships
        }

        # Include legacy project memberships too
        members = db.query(TeamMember).filter(
            TeamMember.active == True
        ).all()

        project_members = [
            member for member in members
            if member.id in member_ids
            or member.project_id == data.project_id
        ]

        # ----------------------------------------------------
        # 5. PREPARE REAL DATABASE INFORMATION
        # ----------------------------------------------------

        project_information = {
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "progress": project.progress
            },

            "sprint_duration_days": data.duration,

            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "priority": task.priority,
                    "status": task.status,
                    "assignee": task.assignee,
                    "story_points": task.points
                }
                for task in tasks
            ],

            "team_members": [
                {
                    "id": member.id,
                    "name": member.name,
                    "role": member.role,
                    "skills": member.skills,
                    "workload": member.workload
                }
                for member in project_members
            ],

            "bugs": [
                {
                    "id": bug.id,
                    "title": bug.title,
                    "description": bug.description,
                    "severity": bug.severity,
                    "status": bug.status
                }
                for bug in bugs
            ]
        }

        # ----------------------------------------------------
        # 6. BUILD GEMINI PROMPT
        # ----------------------------------------------------

        prompt = f"""
You are an expert Agile project manager and senior software
engineering strategist.

Your responsibility is to create a realistic, technically
useful sprint plan using the actual project database information.

PROJECT DATABASE INFORMATION:

{json.dumps(project_information, indent=2, ensure_ascii=False)}

SPRINT DURATION:
{data.duration} days

ADDITIONAL USER INSTRUCTIONS:
{data.instructions or "No additional instructions."}

IMPORTANT PLANNING RULES:

1. Use only the tasks and team members supplied in the data.
2. Never invent task IDs, member IDs, names or existing bugs.
3. Prioritize critical and high-severity open bugs.
4. Prioritize unfinished tasks based on importance, dependencies
   and project progress.
5. Do not recommend completed tasks as unfinished work.
6. Recommend team assignments based on technical skills,
   roles and current workload.
7. Avoid assigning every task to the same person.
8. Consider workload when distributing responsibilities.
9. Identify dependencies and potential blockers.
10. Create a realistic day-by-day sprint schedule.
11. If information is missing, clearly mention the limitation.
12. Do not claim that a task has been completed.
13. This is a recommendation only. Do not modify the database.

Return ONLY valid JSON using this exact structure:

{{
    "sprint_goal": "Main objective of this sprint",

    "executive_summary": "Detailed project-specific sprint overview",

    "recommended_tasks": [
        {{
            "task_id": 1,
            "task_title": "Existing task title",
            "priority": "Critical",
            "recommended_assignee": "Existing team member name",
            "reason": "Why this task and assignment are recommended",
            "estimated_days": 2,
            "dependencies": ["Dependency description"]
        }}
    ],

    "daily_plan": [
        {{
            "day": 1,
            "focus": "Main development focus",
            "activities": [
                "Activity 1",
                "Activity 2"
            ],
            "expected_outcome": "Expected result"
        }}
    ],

    "team_allocation": [
        {{
            "member_name": "Existing team member",
            "role": "Member role",
            "responsibility": "Recommended responsibility",
            "reason": "Reason for assignment"
        }}
    ],

    "risks_and_blockers": [
        {{
            "risk": "Potential risk",
            "impact": "High",
            "mitigation": "Suggested mitigation"
        }}
    ],

    "success_metrics": [
        "Measurable sprint success criterion"
    ],

    "recommendations": [
        "Practical project improvement recommendation"
    ]
}}

Return valid JSON only.
Do not include Markdown or code fences.
Do not invent database records.
"""

        # ----------------------------------------------------
        # 7. GENERATE GEMINI SPRINT PLAN
        # ----------------------------------------------------

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )

        result_text = response.text

        if not result_text:
            raise ValueError("Gemini returned an empty response.")

        result_text = result_text.strip()

        if result_text.startswith("```"):
            result_text = result_text.replace("```json", "", 1)
            result_text = result_text.replace("```", "").strip()

        sprint_plan = json.loads(result_text)

        # ----------------------------------------------------
        # 8. VALIDATE GEMINI RESPONSE
        # ----------------------------------------------------

        required_fields = [
            "sprint_goal",
            "executive_summary",
            "recommended_tasks",
            "daily_plan",
            "team_allocation",
            "risks_and_blockers",
            "success_metrics",
            "recommendations"
        ]

        missing_fields = [
            field for field in required_fields
            if field not in sprint_plan
        ]

        if missing_fields:
            raise ValueError(
                "Gemini response is missing: "
                + ", ".join(missing_fields)
            )

        # ----------------------------------------------------
        # 9. RETURN RESULT
        # ----------------------------------------------------

        return {
            "success": True,
            "ai_powered": True,
            "project_id": project.id,
            "project_name": project.name,
            "sprint_duration": data.duration,
            "total_project_tasks": len(tasks),
            "unfinished_tasks": sum(
                1 for task in tasks
                if task.status.lower() != "done"
            ),
            "team_members_available": len(project_members),
            "total_project_bugs": len(bugs),
            "sprint_plan": sprint_plan
        }

    except HTTPException:
        raise

    except Exception as error:

        print("Gemini Sprint Planner error:", str(error))

        raise HTTPException(
            status_code=502,
            detail="Gemini Sprint Planner failed. Check the backend terminal."
        )

@app.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@app.post("/projects", response_model=ProjectResponse)
def create_project(project_data: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(name=project_data.name, description=project_data.description, status=project_data.status)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project

@app.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.query(Task).filter(Task.project_id == project_id).delete(synchronize_session=False)
    db.delete(project)
    db.commit()
    return {"message": "Project deleted successfully"}

@app.get("/tasks", response_model=list[TaskResponse])
def get_tasks(db: Session = Depends(get_db)):
    return db.query(Task).order_by(Task.id.desc()).all()

@app.post("/tasks", response_model=TaskResponse)
def create_task(task_data: TaskCreate, db: Session = Depends(get_db)):
    project_id = task_data.project_id
    if project_id is None:
        project = db.query(Project).order_by(Project.id.desc()).first()
        if project:
            project_id = project.id
    task = Task(title=task_data.title, description=task_data.description, priority=task_data.priority, status=task_data.status, assignee=task_data.assignee, points=task_data.points, project_id=project_id)
    db.add(task)
    db.commit()
    db.refresh(task)
    sync_project(db, project_id)
    return task

@app.put("/tasks/{task_id}/status", response_model=TaskResponse)
def update_task_status(task_id: int, status: str, db: Session = Depends(get_db)):
    allowed = {"todo", "in_progress", "testing", "done"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid task status")
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = status
    db.commit()
    db.refresh(task)
    sync_project(db, task.project_id)
    return task
@app.put("/tasks/{task_id}/assignee", response_model=TaskResponse)
def update_task_assignee(
    task_id: int,
    assignee: str,
    db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    member = (
        db.query(TeamMember)
        .join(
            TeamProject,
            TeamProject.team_member_id == TeamMember.id
        )
        .filter(
            TeamMember.name == assignee,
            TeamMember.active == True,
            TeamProject.project_id == task.project_id,
            TeamProject.active == True
        )
        .first()
    )

    if not member:
        raise HTTPException(
            status_code=404,
            detail="Active team member is not assigned to this project"
        )

    task.assignee = member.name

    db.commit()
    db.refresh(task)

    sync_project(db, task.project_id)

    log_activity(
        db,
        task.project_id,
        "assigned",
        "task",
        f"Task '{task.title}' assigned to {member.name}"
    )

    return task
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    project_id = task.project_id
    db.delete(task)
    db.commit()
    sync_project(db, project_id)
    return {"message": "Task deleted successfully"}

@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.id.desc()).all()
    tasks = db.query(Task).all()
    total = len(tasks)
    completed = sum(1 for t in tasks if t.status == "done")
    in_work = sum(1 for t in tasks if t.status in {"in_progress", "testing"})
    bugs = sum(int(p.bugs or 0) for p in projects)
    return {
        "active_project": projects[0].name if projects else None,
        "total_tasks": total,
        "completed_tasks": completed,
        "open_bugs": bugs,
        "workload": round(in_work / total * 100) if total else 0,
        "completion_rate": round(completed / total * 100) if total else 0,
        "projects": len(projects)
    }

# ============================================================
# PROJECT PULSE AI ENGINE
# Local deterministic intelligence layer. Designed so a real
# LLM provider can be plugged in later without changing the UI.
# ============================================================
from datetime import datetime, timedelta
import re
import json

class RequirementRequest(BaseModel):
    requirement: str

class CodeRequest(BaseModel):
    prompt: str
    language: str = "python"

class TestRequest(BaseModel):
    functionality: str

class BugRequest(BaseModel):
    bug_id: str = "BUG-001"
    title: str
    severity: str = "Medium"
    description: str = ""

class DocumentationRequest(BaseModel):
    context: str
    doc_type: str = "API Documentation"


def _words(text: str):
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()))


def analyze_requirement_engine(requirement: str):
    text = requirement.strip()
    words = _words(text)
    modules = []
    patterns = [
        (("login", "signup", "account", "authentication", "register", "password"), "Authentication", "User registration, login, sessions and access control."),
        (("search", "filter", "nearby", "location", "restaurant", "catalog"), "Search & Discovery", "Search, filtering, location-aware discovery and browsing."),
        (("product", "menu", "catalog", "item", "inventory"), "Catalog Management", "Products/items, categories, pricing and availability."),
        (("cart", "basket"), "Cart Management", "Add, remove, update quantities and calculate totals."),
        (("order", "booking", "checkout"), "Order Management", "Create orders, validate state transitions and track status."),
        (("payment", "pay", "upi", "card", "transaction"), "Payments", "Payment initiation, verification, failure handling and transaction records."),
        (("delivery", "tracking", "driver", "real-time", "realtime", "map"), "Real-time Tracking", "Live location/status updates and delivery tracking."),
        (("notification", "alert", "email", "sms", "push"), "Notifications", "Event-driven notifications through supported channels."),
        (("admin", "dashboard", "management"), "Administration", "Administrative controls, monitoring and management dashboards."),
        (("report", "analytics", "insight", "metric"), "Analytics & Reporting", "KPIs, reports and decision-support analytics."),
        (("ai", "machine learning", "recommendation", "predict"), "AI Intelligence", "Prediction, recommendations or intelligent automation."),
        (("security", "secure", "role", "permission", "authorization"), "Security & Access Control", "Authorization, validation, audit and security controls."),
    ]
    for keys, name, desc in patterns:
        if any(k in words or k in text.lower() for k in keys):
            modules.append({"name": name, "description": desc})
    if not modules:
        modules = [
            {"name": "Core Application", "description": "Primary business functionality extracted from the requirement."},
            {"name": "Data Layer", "description": "Persistent data model, validation and database operations."},
            {"name": "API & Integration", "description": "Application services and integration interfaces."},
            {"name": "Security", "description": "Validation, authentication and secure access controls."},
        ]
    security = any(x in words or x in text.lower() for x in ["payment", "password", "login", "personal", "security", "admin"])
    realtime = any(x in words or x in text.lower() for x in ["real-time", "realtime", "live", "tracking"])
    complexity_score = min(95, 25 + len(modules) * 7 + (10 if security else 0) + (8 if realtime else 0) + min(15, len(text) // 120))
    complexity = "High" if complexity_score >= 70 else "Medium" if complexity_score >= 45 else "Low"
    priority = "High" if security or len(modules) >= 7 else "Medium"
    stories = []
    for m in modules:
        stories.append({
            "title": f"Implement {m['name']}",
            "description": f"As a user, I want {m['name'].lower()} so the system satisfies the project requirement.",
            "priority": "High" if m["name"] in {"Authentication", "Payments", "Security & Access Control"} else "Medium",
            "points": 8 if m["name"] in {"Payments", "Real-time Tracking", "AI Intelligence"} else 5
        })
    return {
        "summary": text, "modules": modules, "user_stories": stories,
        "complexity": complexity, "complexity_score": complexity_score,
        "priority": priority,
        "security_concern": "Yes — review authentication, validation and protected data flows." if security else "Standard application security controls required.",
        "recommendation": "Agile/Scrum + CI/CD + automated testing" if len(modules) >= 5 else "Agile/Kanban + automated testing",
        "architecture": ["Presentation/UI", "API/Service Layer", "Business Logic", "Database", "External Integrations" if (security or realtime) else "Supporting Services"]
    }

@app.post("/ai/requirements/analyze")
def ai_analyze_requirement(data: RequirementRequest):
    if len(data.requirement.strip()) < 10:
        raise HTTPException(status_code=400, detail="Requirement is too short")
    return analyze_requirement_engine(data.requirement)

@app.post("/ai/tasks/generate")
def ai_generate_tasks(data: RequirementRequest, project_id: int | None = None, persist: bool = True, db: Session = Depends(get_db)):
    if len(data.requirement.strip()) < 10:
        raise HTTPException(status_code=400, detail="Requirement is too short")
    project = db.query(Project).filter(Project.id == project_id).first() if project_id else db.query(Project).order_by(Project.id.desc()).first()
    if not project:
        raise HTTPException(status_code=400 if not project_id else 404, detail="Project not found or create a project first")
    analysis = analyze_requirement_engine(data.requirement)
    created = []
    if persist:
        for story in analysis["user_stories"]:
            task = Task(title=story["title"], description=story["description"], priority=story["priority"], status="todo", assignee="AI Recommended", points=story["points"], project_id=project.id)
            db.add(task)
            created.append(task)
        db.commit()
        for task in created:
            db.refresh(task)
        sync_project(db, project.id)
    return {"project_id": project.id, "project_name": project.name, "analysis": analysis, "created_tasks": [TaskResponse.model_validate(t).model_dump() for t in created]}
@app.post("/ai/code/generate")
def ai_generate_code(data: CodeRequest):
    prompt = data.prompt.strip()
    language = data.language.strip().lower()

    if not prompt:
        raise HTTPException(
            status_code=400,
            detail="Coding prompt is required"
        )

    if not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured"
        )

    language_names = {
        "python": "Python",
        "javascript": "JavaScript",
        "java": "Java",
        "cpp": "C++",
        "html": "HTML",
        "sql": "SQL"
    }

    selected_language = language_names.get(language, language)

    ai_prompt = f"""
You are an expert software developer.

Generate a complete, correct and practical {selected_language} solution.

USER REQUIREMENT:
{prompt}

IMPORTANT RULES:
1. Follow the user's requirement exactly.
2. Generate code in {selected_language} only.
3. Do not return unrelated generic code.
4. Include necessary imports and functions.
5. Use clear variable and function names.
6. Handle important edge cases.
7. Include useful error handling where appropriate.
8. Do not include Markdown code fences.
9. Return only the code, without explanations.
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ai_prompt
        )

        generated_code = (response.text or "").strip()

        if not generated_code:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty code response"
            )

        generated_code = generated_code.replace("```" + selected_language.lower(), "")
        generated_code = generated_code.replace("```", "").strip()

        return {
            "language": language,
            "code": generated_code,
            "quality": {
                "readability": "AI generated",
                "complexity": "Review required",
                "security": "Security review recommended",
                "maintainability": "Review required"
            }
        }

    except HTTPException:
        raise

    except Exception as error:
        print("Gemini code generation error:", error)

        raise HTTPException(
            status_code=502,
            detail="Gemini code generation failed. Please try again."
        )
@app.post("/ai/code/review")
def ai_review_code(data: CodeRequest):
    code = data.prompt.strip()
    language = data.language.strip().lower()

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Please provide code to review"
        )

    if not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured"
        )

    ai_prompt = f"""
You are an experienced software engineer and code reviewer.

Review the following {language} code carefully:

{code}

Return ONLY valid JSON in this exact format:
{{
    "score": 85,
    "summary": "A short overall review",
    "issues": [
        {{
            "severity": "High",
            "issue": "Description of the issue",
            "recommendation": "How to fix it"
        }}
    ]
}}

Rules:
- Give an honest quality score from 0 to 100.
- Identify actual bugs, security risks, poor practices and performance problems.
- Do not invent issues.
- If no significant issues are found, return an empty issues array.
- Use Critical, High, Medium, Low or Info for severity.
- Return valid JSON only. No Markdown or code fences.
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ai_prompt
        )

        result_text = (response.text or "").strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()

        result = json.loads(result_text)

        return {
            "issues": result.get("issues", []),
            "score": max(0, min(100, int(result.get("score", 0)))),
            "summary": result.get("summary", "AI code review completed.")
        }

    except HTTPException:
        raise

    except Exception as error:
        print("Gemini code review error:", error)

        raise HTTPException(
            status_code=502,
            detail="AI code review failed. Please try again."
        )
@app.post("/ai/code/explain")
def ai_explain_code(data: CodeRequest):
    code = data.prompt.strip()
    language = data.language.strip().lower()

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Please provide code to explain"
        )

    if not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured"
        )

    ai_prompt = f"""
You are a friendly programming teacher.

Explain this {language} code in simple language:

{code}

Include:
1. What the program does.
2. Explanation of important lines and functions.
3. How the program executes step by step.
4. Important programming concepts used.
5. A simple example of its output, if possible.

Make the explanation clear and beginner-friendly.
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ai_prompt
        )

        explanation = (response.text or "").strip()

        if not explanation:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty explanation"
            )

        return {"explanation": explanation}

    except HTTPException:
        raise

    except Exception as error:
        print("Gemini explanation error:", error)

        raise HTTPException(
            status_code=502,
            detail="Code explanation failed. Please try again."
        )    
@app.post("/ai/code/debug")
def ai_debug_code(data: CodeRequest):
    code = data.prompt.strip()
    language = data.language.strip().lower()

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Please provide code to debug"
        )

    if not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured"
        )

    ai_prompt = f"""
You are an expert software debugging assistant.

Analyze this {language} code:

{code}

Provide:
1. Errors and bugs found.
2. Why each problem occurs.
3. The corrected complete code.
4. Explanation of the corrections.
5. Suggestions to prevent similar errors.

If no errors are found, clearly state that.
Use simple language and do not invent errors.
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ai_prompt
        )

        result = (response.text or "").strip()

        if not result:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty debugging result"
            )

        return {"debug_result": result}

    except HTTPException:
        raise

    except Exception as error:
        print("Gemini debugging error:", error)

        raise HTTPException(
            status_code=502,
            detail="AI debugging failed. Please try again."
        )
@app.post("/ai/code/optimize")
def ai_optimize_code(data: CodeRequest):
    code = data.prompt.strip()
    language = data.language.strip().lower()

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Please provide code to optimize"
        )

    if not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured"
        )

    ai_prompt = f"""
You are an expert software optimization engineer.

Optimize this {language} code:

{code}

Provide:
1. Improved complete code.
2. Performance improvements.
3. Readability improvements.
4. Memory efficiency improvements where applicable.
5. Explanation of the changes.

Rules:
- Preserve the original functionality.
- Do not remove required features.
- Avoid unnecessary complexity.
- Return beginner-friendly explanations.
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ai_prompt
        )

        result = (response.text or "").strip()

        if not result:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty optimization result"
            )

        return {"optimization_result": result}

    except HTTPException:
        raise

    except Exception as error:
        print("Gemini optimization error:", error)

        raise HTTPException(
            status_code=502,
            detail="Code optimization failed. Please try again."
        ) 
@app.post("/ai/code/tests")
def ai_generate_code_tests(data: CodeRequest):
    code = data.prompt.strip()
    language = data.language.strip().lower()

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Please provide code to generate tests"
        )

    if not gemini_client:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is not configured"
        )

    ai_prompt = f"""
You are an expert software testing engineer.

Generate unit tests for this {language} code:

{code}

Requirements:
1. Generate complete, executable unit tests.
2. Cover normal inputs and expected outputs.
3. Include edge cases and invalid inputs.
4. Test important functions independently.
5. Use the standard testing framework for {language}.
6. Include necessary imports.
7. Add a short explanation of how to run the tests.
8. Do not invent functions that do not exist in the supplied code.

Return the tests and explanation in a clear format.
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=ai_prompt
        )

        result = (response.text or "").strip()

        if not result:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty test result"
            )

        return {"tests": result}

    except HTTPException:
        raise

    except Exception as error:
        print("Gemini test generation error:", error)

        raise HTTPException(
            status_code=502,
            detail="AI test generation failed. Please try again."
        )
           
@app.post("/ai/tests/generate")
def ai_generate_tests(data: TestRequest):
    f = data.functionality.strip()
    if not f:
        raise HTTPException(status_code=400, detail="Functionality is required")
    return {"functionality": f, "tests": [
        {"type": "Positive", "title": "Valid input", "expected": "System completes the requested operation successfully."},
        {"type": "Negative", "title": "Invalid input", "expected": "System rejects invalid data with a clear validation message."},
        {"type": "Boundary", "title": "Minimum and maximum values", "expected": "System handles supported limits without errors."},
        {"type": "Empty", "title": "Missing required input", "expected": "System prevents submission and identifies required fields."},
        {"type": "Security", "title": "Malicious input", "expected": "System validates/sanitizes input and prevents unauthorized behavior."},
        {"type": "Concurrency", "title": "Repeated or simultaneous requests", "expected": "System maintains data consistency and correct state."},
        {"type": "Recovery", "title": "Dependency/service failure", "expected": "System handles failure gracefully and reports a useful error."},
        {"type": "Usability", "title": "Successful user flow", "expected": "User receives clear status/confirmation after completion."}
    ]}


@app.get("/projects")
def get_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.id.asc()).all()

    return [
        {
            "id": project.id,
            "name": project.name,
            "status": project.status
        }
        for project in projects
    ]


@app.get("/bugs")
def get_bugs(project_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(Bug)

    if project_id is not None:
        query = query.filter(Bug.project_id == project_id)

    bugs = query.order_by(Bug.created_at.desc()).all()

    return [
        {
            "id": bug.id,
            "bug_id": f"BUG-{bug.id:03d}",
            "project_id": bug.project_id,
            "title": bug.title,
            "description": bug.description,
            "severity": bug.severity,
            "status": bug.status,
            "probable_area": bug.probable_area,
            "root_cause": bug.root_cause,
            "created_at": bug.created_at.isoformat() if bug.created_at else None
        }
        for bug in bugs
    ]

@app.get("/bugs/summary")
def get_bug_summary(db: Session = Depends(get_db)):
    bugs = db.query(Bug).all()

    total = len(bugs)
    open_bugs = sum(1 for b in bugs if b.status.lower() == "open")
    resolved = sum(1 for b in bugs if b.status.lower() == "resolved")
    critical = sum(1 for b in bugs if b.severity.lower() == "critical")
    high = sum(1 for b in bugs if b.severity.lower() == "high")

    return {
        "total": total,
        "open": open_bugs,
        "resolved": resolved,
        "critical": critical,
        "high": high,
        "resolution_rate": round((resolved / total) * 100, 1) if total else 0
    }


@app.post("/ai/bugs/analyze")
def ai_analyze_bug(data: BugRequest):

    if not gemini_client:
        raise HTTPException(
            status_code=503,
            detail="Gemini is not configured. Check GEMINI_API_KEY in your .env file."
        )

    prompt = f"""
You are a senior software debugging engineer performing a real technical
investigation of a software defect.

Investigate the following bug carefully. Do not give generic answers.
Base your findings on the supplied information. Clearly distinguish
confirmed facts from hypotheses. Never invent logs, code, or evidence.

BUG DETAILS
Bug ID: {data.bug_id}
Title: {data.title}
Severity: {data.severity}
Description: {data.description}

Perform these investigation tasks:

1. Identify the most likely affected software component or module.
2. Explain the most likely technical root cause in detail.
3. Explain the evidence from the bug description supporting your diagnosis.
4. Identify possible affected components or dependencies.
5. Give specific step-by-step debugging instructions.
6. Recommend a practical technical fix.
7. Provide regression test cases to prevent the issue from happening again.
8. Estimate diagnostic confidence from 0 to 100 based on the available evidence.
   Lower confidence when important technical information is missing.

Return ONLY valid JSON in this exact structure:

{{
    "probable_area": "Affected module or component",
    "root_cause": "Detailed technical root cause",
    "evidence": "Evidence supporting the diagnosis",
    "affected_components": ["Component 1", "Component 2"],
    "investigation_summary": "Detailed investigation findings",
    "debugging_steps": [
        "Step 1",
        "Step 2",
        "Step 3"
    ],
    "recommended_fix": "Specific technical solution",
    "regression_tests": [
        "Test case 1",
        "Test case 2",
        "Test case 3"
    ],
    "suggested_action": "Most important immediate action",
    "confidence": 0,
    "confidence_reason": "Why this confidence level was selected"
}}

Do not return Markdown or code fences. Return valid JSON only.
"""

    try:

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )

        result_text = response.text

        if not result_text:
            raise ValueError("Gemini returned an empty response")

        result_text = result_text.strip()

        if result_text.startswith("```"):
            result_text = result_text.replace("```json", "", 1)
            result_text = result_text.replace("```", "").strip()

        investigation = json.loads(result_text)

        required_fields = [
            "probable_area",
            "root_cause",
            "evidence",
            "investigation_summary",
            "debugging_steps",
            "recommended_fix",
            "regression_tests",
            "suggested_action",
            "confidence"
        ]

        missing_fields = [
            field for field in required_fields
            if field not in investigation
        ]

        if missing_fields:
            raise ValueError(
                "Gemini response is missing: " + ", ".join(missing_fields)
            )

        confidence = max(
            0,
            min(100, int(investigation["confidence"]))
        )

        return {
            "success": True,
            "ai_powered": True,
            "bug_id": data.bug_id,
            "severity": data.severity,
            "probable_area": investigation["probable_area"],
            "root_cause": investigation["root_cause"],
            "evidence": investigation["evidence"],
            "affected_components": investigation["affected_components"],
            "investigation_summary": investigation["investigation_summary"],
            "debugging_steps": investigation["debugging_steps"],
            "recommended_fix": investigation["recommended_fix"],
            "regression_tests": investigation["regression_tests"],
            "suggested_action": investigation["suggested_action"],
            "confidence": confidence,
            "confidence_reason": investigation.get("confidence_reason", "")
        }

    except Exception as error:

        print("Gemini bug investigation error:", str(error))

        raise HTTPException(
            status_code=502,
            detail="Gemini bug investigation failed. Check the backend terminal and model configuration."
        )

@app.post("/ai/documentation/generate")
def ai_generate_documentation(data: DocumentationRequest):
    context = data.context.strip()
    if not context:
        raise HTTPException(status_code=400, detail="Documentation context is required")
    title = context.split(".")[0][:80] or "Project Documentation"
    return {"doc_type": data.doc_type, "title": title, "content": f'''<h3>{title}</h3>
<p>AI-generated {data.doc_type.lower()} based on the supplied software context.</p>
<h4>Overview</h4><p>{context}</p>
<h4>Key Responsibilities</h4><ul><li>Validate incoming data.</li><li>Apply business rules.</li><li>Handle errors consistently.</li><li>Return structured responses.</li></ul>
<h4>Quality & Security</h4><p>Use authentication, authorization, validation, logging, automated tests and secure configuration before production release.</p>
<h4>Maintenance</h4><p>Keep this documentation synchronized with implementation and API changes.</p>'''}

@app.get("/ai/risks/scan")
def ai_scan_risks(project_id: int | None = None, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first() if project_id else db.query(Project).order_by(Project.id.desc()).first()
    tasks = db.query(Task).filter(Task.project_id == project.id).all() if project else db.query(Task).all()
    risks = []
    total = len(tasks)
    active = sum(1 for t in tasks if t.status in {"in_progress", "testing"})
    high = sum(1 for t in tasks if str(t.priority).lower() == "high")
    waiting = sum(1 for t in tasks if t.status == "todo" and str(t.priority).lower() == "high")
    if total == 0: risks.append({"level": "MEDIUM", "score": 55, "title": "Insufficient project execution data", "description": "The project has no tasks yet, so delivery health cannot be measured reliably."})
    if high >= 3: risks.append({"level": "HIGH", "score": 78, "title": "High-priority task concentration", "description": f"{high} high-priority tasks require attention."})
    if total and active / total > 0.65: risks.append({"level": "HIGH", "score": 74, "title": "Large active-work queue", "description": "A high proportion of tasks are currently in progress or testing."})
    if waiting: risks.append({"level": "MEDIUM", "score": 62, "title": "High-priority work waiting to start", "description": f"{waiting} high-priority task(s) are still in To Do."})
    if not risks: risks.append({"level": "LOW", "score": 24, "title": "No major execution risk detected", "description": "Current task distribution does not indicate a major delivery risk."})
    health = max(0, 100 - round(sum(r["score"] for r in risks) / len(risks)))
    return {"project": project.name if project else None, "health": health, "risks": risks, "scanned_at": datetime.utcnow().isoformat()}

@app.get("/analytics")
def analytics(db: Session = Depends(get_db)):
    projects = db.query(Project).all(); tasks = db.query(Task).all()
    total = len(tasks); done = sum(1 for t in tasks if t.status == "done")
    points_total = sum(int(t.points or 0) for t in tasks); points_done = sum(int(t.points or 0) for t in tasks if t.status == "done")
    by_status = {s: sum(1 for t in tasks if t.status == s) for s in ["todo", "in_progress", "testing", "done"]}
    by_priority = {p: sum(1 for t in tasks if str(t.priority).lower() == p.lower()) for p in ["High", "Medium", "Low"]}
    return {"projects": len(projects), "tasks": total, "completed": done, "completion_rate": round(done / total * 100) if total else 0, "story_points": points_total, "completed_points": points_done, "velocity": points_done, "status": by_status, "priority": by_priority, "active_workload": round((by_status["in_progress"] + by_status["testing"]) / total * 100) if total else 0}
# =========================================================
# AI TEAM ASSIGNMENT
# =========================================================

@app.post("/ai/team/assign")
def ai_team_assignment(
    task_id: int,
    db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    members = (
        db.query(TeamMember)
        .filter(
            TeamMember.active == True,
            TeamMember.project_id == task.project_id
        )
        .all()
    )

    if not members:
        raise HTTPException(
            status_code=404,
            detail="No active team members found for this project"
        )

    task_text = (
        f"{task.title} {task.description or ''}"
    ).lower()

    skill_keywords = {
        "python": ["python", "fastapi", "django"],
        "backend": ["backend", "api", "server"],
        "frontend": ["frontend", "react", "html", "css", "javascript", "ui"],
        "database": ["database", "sql", "mysql", "postgres", "sqlite"],
        "testing": ["test", "testing", "qa", "automation"],
        "security": ["security", "authentication", "authorization"],
        "ai": ["ai", "machine learning", "ml", "model"],
        "devops": ["docker", "deployment", "devops", "cloud"],
        "payment": ["payment", "gateway", "razorpay", "stripe"]
    }

    required_skills = []

    for skill, keywords in skill_keywords.items():
        if any(keyword in task_text for keyword in keywords):
            required_skills.append(skill)

    candidates = []

    for member in members:

        member_skills = (member.skills or "").lower()
        role = (member.role or "").lower()
        combined_skills = member_skills + " " + role

        matched_skills = [
            skill for skill in required_skills
            if any(
                keyword in combined_skills
                for keyword in skill_keywords[skill]
            )
        ]

        if required_skills:
            skill_match = round(
                len(matched_skills) / len(required_skills) * 100
            )
        else:
            skill_match = 60

        assigned_tasks = db.query(Task).filter(
            Task.project_id == task.project_id,
            Task.assignee == member.name,
            Task.status != "done",
            Task.id != task.id
        ).count()

        completed_tasks = db.query(Task).filter(
            Task.project_id == task.project_id,
            Task.assignee == member.name,
            Task.status == "done"
        ).count()

        workload_score = max(
            0,
            100 - assigned_tasks * 15
        )

        final_score = round(
            skill_match * 0.7 + workload_score * 0.3
        )

        candidates.append({
            "id": member.id,
            "name": member.name,
            "role": member.role,
            "skills": member.skills,
            "active_tasks": assigned_tasks,
            "completed_tasks": completed_tasks,
            "skill_match": skill_match,
            "workload_score": workload_score,
            "assignment_score": final_score,
            "matched_skills": matched_skills
        })

    candidates.sort(
        key=lambda x: x["assignment_score"],
        reverse=True
    )

    top_candidates = candidates[:3]
    best = top_candidates[0]

    reason = (
        f"{best['name']} matches "
        f"{len(best['matched_skills'])} required skills "
        f"and has {best['active_tasks']} other active tasks."
    )

    return {
        "success": True,
        "task": {
            "id": task.id,
            "title": task.title,
            "priority": task.priority
        },
        "recommended_member": {
            "id": best["id"],
            "name": best["name"],
            "role": best["role"],
            "skills": best["skills"],
            "workload": best["active_tasks"]
        },
        "skill_match": best["skill_match"],
        "assignment_score": best["assignment_score"],
        "required_skills": required_skills,
        "matched_skills": best["matched_skills"],
        "reason": reason,
        "ai_recommendation": (
            f"Assign '{task.title}' to {best['name']}."
        ),
        "candidates": top_candidates
    }

# ============================================================
# PROJECT PULSE — WORKFLOW / TEAM / REQUIREMENTS / BUGS
# ============================================================

class RequirementCreate(BaseModel):
    project_id: int | None = None
    title: str = "Project Requirement"
    content: str
    status: str = "Analyzed"

class RequirementResponse(BaseModel):
    id: int
    project_id: int | None
    title: str
    content: str
    status: str
    complexity: str
    priority: str
    analysis: dict
    created_at: str

class TeamMemberCreate(BaseModel):
    name: str
    role: str = "Developer"
    skills: str = "Python, APIs"
    project_id: int | None = None
    workload: int = 0

class TeamMemberResponse(BaseModel):
    id: int
    name: str
    role: str
    skills: str
    project_id: int | None
    workload: int
    active: bool

class BugCreate(BaseModel):
    project_id: int | None = None
    title: str
    description: str = ""
    severity: str = "Medium"
    status: str = "Open"
    created_at: datetime | None = None

class BugResponse(BaseModel):
    id: int
    project_id: int | None
    title: str
    description: str
    severity: str
    status: str
    probable_area: str
    root_cause: str
    created_at: str

class ActivityResponse(BaseModel):
    id: int
    project_id: int | None
    action: str
    entity_type: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True

class WorkflowRequest(BaseModel):
    project_id: int | None = None
    title: str = "AI Generated Requirement"
    requirement: str

class AIProjectGenerateRequest(BaseModel):
    project_id: int | None = None
    project_name: str
    description: str
    project_type: str = "Web Application"
    team_size: int = 5
    deadline_days: int = 30
    technology: str = "AI Recommended"
    generate_modules: bool = True
    generate_stories: bool = True
    generate_tasks: bool = True
    generate_priorities: bool = True
    generate_risks: bool = True
    generate_assignments: bool = True
    generate_testing: bool = True
    generate_documentation: bool = True


def get_project_or_latest(db: Session, project_id: int | None):
    if project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project
    return db.query(Project).order_by(Project.id.desc()).first()


def log_activity(db: Session, project_id: int | None, action: str, entity_type: str, message: str):
    item = Activity(project_id=project_id, action=action, entity_type=entity_type, message=message)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def sync_members(db: Session, project_id: int | None):
    if not project_id:
        return
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        project.members = db.query(TeamMember).filter(TeamMember.project_id == project_id, TeamMember.active == True).count()
        db.commit()


@app.get("/requirements", response_model=list[RequirementResponse])
def get_requirements(project_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Requirement)
    if project_id:
        q = q.filter(Requirement.project_id == project_id)
    rows = q.order_by(Requirement.id.desc()).all()
    return [RequirementResponse(id=r.id, project_id=r.project_id, title=r.title, content=r.content,
        status=r.status, complexity=r.complexity, priority=r.priority,
        analysis=json.loads(r.analysis_json or "{}"), created_at=r.created_at.isoformat()) for r in rows]


@app.post("/requirements", response_model=RequirementResponse)
def create_requirement(data: RequirementCreate, db: Session = Depends(get_db)):
    if len(data.content.strip()) < 10:
        raise HTTPException(status_code=400, detail="Requirement is too short")
    project = get_project_or_latest(db, data.project_id)
    analysis = analyze_requirement_engine(data.content)
    row = Requirement(project_id=project.id if project else None, title=data.title, content=data.content,
                      status=data.status, complexity=analysis["complexity"], priority=analysis["priority"],
                      analysis_json=json.dumps(analysis))
    db.add(row)
    db.commit(); db.refresh(row)
    log_activity(db, row.project_id, "created", "requirement", f"Requirement analyzed: {row.title}")
    return RequirementResponse(id=row.id, project_id=row.project_id, title=row.title, content=row.content,
        status=row.status, complexity=row.complexity, priority=row.priority,
        analysis=analysis, created_at=row.created_at.isoformat())


@app.get("/team", response_model=list[TeamMemberResponse])
def get_team(project_id: int | None = None, db: Session = Depends(get_db)):

    q = db.query(TeamMember)

    if project_id is not None:
        q = q.join(
            TeamProject,
            TeamProject.team_member_id == TeamMember.id
        ).filter(
            TeamProject.project_id == project_id,
            TeamProject.active == True,
            TeamMember.active == True
        )

    members = q.order_by(TeamMember.id.desc()).all()

    if project_id is not None:
        return [
            {
                "id": m.id,
                "name": m.name,
                "role": m.role,
                "skills": m.skills,
                "project_id": project_id,
                "workload": m.workload,
                "active": m.active
            }
            for m in members
        ]

    return members

@app.post("/team/{member_id}/projects/{project_id}")
def assign_member_to_project(
    member_id: int,
    project_id: int,
    db: Session = Depends(get_db)
):
    member = db.query(TeamMember).filter(
        TeamMember.id == member_id
    ).first()

    if not member:
        raise HTTPException(
            status_code=404,
            detail="Team member not found"
        )

    if not member.active:
        raise HTTPException(
            status_code=400,
            detail="Team member is inactive"
        )

    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    membership = db.query(TeamProject).filter(
        TeamProject.team_member_id == member_id,
        TeamProject.project_id == project_id
    ).first()

    if membership:
        if membership.active:
            return {"message": "Member already assigned to this project"}

        membership.active = True
    else:
        db.add(TeamProject(
            team_member_id=member_id,
            project_id=project_id,
            active=True
        ))

    db.flush()

    project.members = db.query(TeamProject).filter(
        TeamProject.project_id == project_id,
        TeamProject.active == True
    ).count()

    log_activity(
        db,
        project_id,
        "assigned",
        "team",
        f"{member.name} assigned to project {project.name}"
    )

    db.commit()

    return {
        "success": True,
        "message": f"{member.name} assigned to {project.name}",
        "member_id": member_id,
        "project_id": project_id,
        "project_name": project.name
    }

@app.post("/team", response_model=TeamMemberResponse)
def create_team_member(data: TeamMemberCreate, db: Session = Depends(get_db)):

    project = get_project_or_latest(db, data.project_id)

    member = TeamMember(
        name=data.name,
        role=data.role,
        skills=data.skills,
        project_id=project.id if project else None,
        workload=max(0, min(100, data.workload))
    )

    db.add(member)
    db.flush()

    if project:
        membership = TeamProject(
            team_member_id=member.id,
            project_id=project.id,
            active=True
        )
        db.add(membership)

    db.commit()
    db.refresh(member)

    sync_members(db, member.project_id)

    log_activity(
        db,
        member.project_id,
        "added",
        "team",
        f"Team member added: {member.name}"
    )

    return member


@app.delete("/team/{member_id}")
def delete_team_member(member_id: int, db: Session = Depends(get_db)):
    member = db.query(TeamMember).filter(TeamMember.id == member_id).first()
    if not member: raise HTTPException(status_code=404, detail="Team member not found")
    pid = member.project_id
    db.delete(member); db.commit(); sync_members(db, pid)
    log_activity(db, pid, "removed", "team", "A team member was removed")
    return {"message": "Team member deleted"}


@app.get("/bugs", response_model=list[BugResponse])
def get_bugs(project_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Bug)
    if project_id: q = q.filter(Bug.project_id == project_id)
    rows = q.order_by(Bug.id.desc()).all()
    return [BugResponse(id=b.id, project_id=b.project_id, title=b.title, description=b.description,
        severity=b.severity, status=b.status, probable_area=b.probable_area, root_cause=b.root_cause,
        created_at=b.created_at.isoformat()) for b in rows]

@app.post("/bugs", response_model=BugResponse)
def create_bug(data: BugCreate, db: Session = Depends(get_db)):
    project = get_project_or_latest(db, data.project_id)

    text = (data.title + " " + data.description).lower()

    if any(k in text for k in ["login", "password", "auth"]):
        area, cause = "Authentication / API", "Input validation, encoding or session handling"
    elif any(k in text for k in ["cart", "price", "total", "amount"]):
        area, cause = "Cart / Calculation Service", "Calculation, rounding or stale state"
    elif any(k in text for k in ["map", "location", "tracking", "gps"]):
        area, cause = "Tracking / External API", "API response, permission or network failure"
    elif any(k in text for k in ["database", "sql", "record", "save"]):
        area, cause = "Database Layer", "Query, schema, transaction or validation issue"
    else:
        area, cause = "Application Service", "Business logic or integration failure"

    bug = Bug(
        project_id=project.id if project else None,
        title=data.title,
        description=data.description,
        severity=data.severity,
        status=data.status,
        probable_area=area,
        root_cause=cause,
        created_at=data.created_at or datetime.utcnow()
    )

    db.add(bug)
    db.commit()
    db.refresh(bug)

    if project:
        project.bugs = db.query(Bug).filter(
            Bug.project_id == project.id,
            Bug.status != "Resolved"
        ).count()
        db.commit()

    log_activity(
        db,
        bug.project_id,
        "reported",
        "bug",
        f"Bug reported: {bug.title}"
    )

    return BugResponse(
        id=bug.id,
        project_id=bug.project_id,
        title=bug.title,
        description=bug.description,
        severity=bug.severity,
        status=bug.status,
        probable_area=area,
        root_cause=cause,
        created_at=bug.created_at.isoformat()
    )
@app.post("/bugs/distribute-dates")
def distribute_bug_dates(db: Session = Depends(get_db)):

    bugs = db.query(Bug).order_by(Bug.id.asc()).all()

    total = len(bugs)

    if total == 0:
        return {"message": "No bugs found"}

    start_date = datetime(2026, 10, 30, 9, 0, 0)

    for index, bug in enumerate(bugs):

        if total == 1:
            day_offset = 0
        else:
            day_offset = round(index * 14 / (total - 1))

        bug.created_at = start_date + timedelta(
            days=day_offset,
            hours=index % 8
        )

    db.commit()

    return {
        "success": True,
        "message": "Bug dates distributed successfully",
        "total_updated": total,
        "start_date": "2026-10-30",
        "end_date": "2026-11-13"
    }
@app.get("/activity", response_model=list[ActivityResponse])
def get_activity(project_id: int | None = None, limit: int = 20, db: Session = Depends(get_db)):
    q = db.query(Activity)
    if project_id: q = q.filter(Activity.project_id == project_id)
    rows = q.order_by(Activity.id.desc()).limit(min(max(limit, 1), 100)).all()
    return rows


@app.get("/projects/{project_id}/health")
def project_health(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project: raise HTTPException(status_code=404, detail="Project not found")
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    bugs = db.query(Bug).filter(Bug.project_id == project_id, Bug.status != "Resolved").all()
    total = len(tasks); done = sum(t.status == "done" for t in tasks)
    active = sum(t.status in {"in_progress", "testing"} for t in tasks)
    high = sum(str(t.priority).lower() == "high" for t in tasks)
    score = 100
    score -= min(35, len(bugs) * 10)
    score -= min(25, high * 5)
    score -= 15 if total and active / total > .7 else 0
    score += round(done / total * 15) if total else 0
    score = max(0, min(100, score))
    label = "Excellent" if score >= 85 else "Healthy" if score >= 70 else "Attention Needed" if score >= 50 else "Critical"
    return {"project_id": project_id, "project": project.name, "score": score, "label": label,
            "tasks": total, "completed": done, "active": active, "open_bugs": len(bugs), "high_priority": high}


@app.post("/ai/workflow/run")
def run_full_ai_workflow(data: WorkflowRequest, db: Session = Depends(get_db)):
    if len(data.requirement.strip()) < 10:
        raise HTTPException(status_code=400, detail="Requirement is too short")
    project = get_project_or_latest(db, data.project_id)
    if not project: raise HTTPException(status_code=400, detail="Create a project first")
    analysis = analyze_requirement_engine(data.requirement)
    req = Requirement(project_id=project.id, title=data.title or "AI Generated Requirement", content=data.requirement,
                      status="Analyzed", complexity=analysis["complexity"], priority=analysis["priority"], analysis_json=json.dumps(analysis))
    db.add(req); db.flush()
    created=[]
    for story in analysis["user_stories"]:
        task=Task(title=story["title"], description=story["description"], priority=story["priority"],
                  status="todo", assignee="AI Recommended", points=story["points"], project_id=project.id)
        db.add(task); created.append(task)
    db.commit()
    for t in created: db.refresh(t)
    sync_project(db, project.id)
    log_activity(db, project.id, "executed", "ai_workflow", f"AI workflow generated {len(created)} tasks from requirement")
    return {"project": {"id":project.id,"name":project.name}, "requirement_id":req.id,
            "analysis":analysis, "tasks":[TaskResponse.model_validate(t).model_dump() for t in created],
            "next_steps":["Assign generated tasks to team members", "Generate tests for critical modules", "Run project risk scan", "Review AI-generated code before production"]}


# ============================================================
# AI PROJECT GENERATOR — ORCHESTRATED PROJECT CREATION
# ============================================================

def _role_for_module(module_name: str):
    n = module_name.lower()
    if any(k in n for k in ["payment", "order", "cart", "api", "security"]):
        return "Backend Developer"
    if any(k in n for k in ["search", "catalog", "notification", "tracking"]):
        return "Frontend Developer"
    if "ai" in n or "analytics" in n:
        return "AI Engineer"
    return "Full Stack Developer"


def _task_templates(module_name: str, project_type: str):
    role = _role_for_module(module_name)
    return [
        (f"Design {module_name} architecture", "High", role, 3),
        (f"Implement {module_name} functionality", "High", role, 5),
        (f"Test {module_name} and handle edge cases", "Medium", "QA Engineer", 3),
    ]


@app.post("/ai/project/generate")
def ai_generate_project(data: AIProjectGenerateRequest, db: Session = Depends(get_db)):
    description = data.description.strip()
    name = data.project_name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Project name is required")
    if len(description) < 10:
        raise HTTPException(status_code=400, detail="Project description must be at least 10 characters")
    if data.team_size < 1 or data.team_size > 100:
        raise HTTPException(status_code=400, detail="Team size must be between 1 and 100")
    if data.deadline_days < 1:
        raise HTTPException(status_code=400, detail="Deadline must be at least 1 day")

    project = db.query(Project).filter(Project.id == data.project_id).first() if data.project_id else None
    if not project:
        project = Project(name=name, description=description, status="AI Planning")
        db.add(project); db.commit(); db.refresh(project)
    else:
        project.name = name or project.name
        project.description = description
        project.status = "AI Planning"
        db.commit()

    analysis = analyze_requirement_engine(description)
    modules_data = analysis["modules"] if data.generate_modules else []
    module_rows = []
    story_rows = []
    task_rows = []
    risk_rows = []

    # If regenerating an existing project, remove only previous generated planning data.
    db.query(ProjectRisk).filter(ProjectRisk.project_id == project.id).delete(synchronize_session=False)
    db.query(UserStory).filter(UserStory.project_id == project.id).delete(synchronize_session=False)
    db.query(ProjectModule).filter(ProjectModule.project_id == project.id).delete(synchronize_session=False)
    db.commit()

    for index, module in enumerate(modules_data, start=1):
        m = ProjectModule(project_id=project.id, name=module["name"], description=module["description"], risk_level="High" if module["name"] in {"Payments", "Security & Access Control", "Real-time Tracking"} else "Medium", progress=0)
        db.add(m); db.flush(); module_rows.append(m)

        if data.generate_stories:
            story_title = f"{module['name']} core user capability"
            story_priority = "High" if module["name"] in {"Authentication", "Payments", "Security & Access Control", "Order Management"} else "Medium"
            points = 8 if module["name"] in {"Payments", "Real-time Tracking", "AI Intelligence"} else 5
            story = UserStory(project_id=project.id, module_id=m.id, code=f"US-{index:03d}", title=story_title,
                              description=f"As a user, I want {module['name'].lower()} so that the platform satisfies the stated project requirement.",
                              priority=story_priority if data.generate_priorities else "Medium", points=points)
            db.add(story); db.flush(); story_rows.append(story)
            m.story_count = 1

            if data.generate_tasks:
                for title, priority, assignee, points_task in _task_templates(module["name"], data.project_type):
                    if not data.generate_priorities: priority = "Medium"
                    if not data.generate_assignments: assignee = "AI Recommended"
                    task = Task(title=title, description=f"AI-generated task for {module['name']} in {data.project_type}.",
                                priority=priority, status="todo", assignee=assignee, points=points_task, project_id=project.id)
                    db.add(task); task_rows.append(task)
                m.task_count = 3

    if data.generate_risks:
        risk_specs = []
        low = description.lower()
        if any(k in low for k in ["payment", "upi", "card", "transaction"]):
            risk_specs.append(("Payment Integration", "External payment failures or delayed callbacks can interrupt order completion.", 72, "High", "HIGH", "Add retry handling, transaction verification and clear failure states."))
        if any(k in low for k in ["real-time", "realtime", "tracking", "map", "gps"]):
            risk_specs.append(("Real-time Synchronization", "Live tracking depends on reliable network and event synchronization.", 61, "High", "HIGH", "Use resilient event updates, retries and stale-state detection."))
        if len(modules_data) >= 6:
            risk_specs.append(("Integration Complexity", "Multiple modules and dependencies increase integration and regression risk.", 58, "Medium", "MEDIUM", "Define API contracts and integrate incrementally with automated tests."))
        risk_specs.append(("Delivery Timeline", f"The planned {data.deadline_days}-day deadline requires disciplined prioritization and sprint planning.", 49 if data.deadline_days >= 30 else 68, "Medium" if data.deadline_days >= 30 else "High", "MEDIUM" if data.deadline_days >= 30 else "HIGH", "Prioritize MVP scope, track velocity and review blockers every sprint."))
        for i, (title, desc, probability, impact, level, recommendation) in enumerate(risk_specs[:6], start=1):
            risk = ProjectRisk(project_id=project.id, code=f"RISK-{i:03d}", title=title, description=desc,
                               probability=probability, impact=impact, level=level, recommendation=recommendation)
            db.add(risk); risk_rows.append(risk)

    project.status = "In Development"
    db.commit()
    for row in task_rows: db.refresh(row)
    for row in module_rows: db.refresh(row)
    for row in story_rows: db.refresh(row)
    for row in risk_rows: db.refresh(row)
    sync_project(db, project.id)
    log_activity(db, project.id, "generated", "ai_project", f"AI Project Generator created {len(module_rows)} modules, {len(story_rows)} stories and {len(task_rows)} tasks")

    high_tasks = sum(1 for t in task_rows if str(t.priority).lower() == "high")
    health = max(55, min(96, 92 - len(risk_rows) * 4 - max(0, len(module_rows)-8)*2))
    return {
        "project": {"id": project.id, "name": project.name, "description": project.description, "status": project.status},
        "summary": {"modules": len(module_rows), "stories": len(story_rows), "tasks": len(task_rows), "risks": len(risk_rows), "high_priority_tasks": high_tasks, "health": health, "deadline_days": data.deadline_days, "team_size": data.team_size, "technology": data.technology},
        "modules": [{"id":m.id,"name":m.name,"description":m.description,"story_count":m.story_count,"task_count":m.task_count,"risk_level":m.risk_level,"progress":m.progress} for m in module_rows],
        "user_stories": [{"id":s.id,"code":s.code,"module_id":s.module_id,"title":s.title,"description":s.description,"priority":s.priority,"points":s.points} for s in story_rows],
        "tasks": [TaskResponse.model_validate(t).model_dump() for t in task_rows],
        "risks": [{"id":r.id,"code":r.code,"title":r.title,"description":r.description,"probability":r.probability,"impact":r.impact,"level":r.level,"recommendation":r.recommendation} for r in risk_rows],
        "ai_insight": f"Prioritize {high_tasks} high-priority tasks, monitor {len(risk_rows)} identified risks and keep the first release focused on core user value.",
    }


@app.get("/projects/{project_id}/ai-plan")
def get_ai_project_plan(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project: raise HTTPException(status_code=404, detail="Project not found")
    modules = db.query(ProjectModule).filter(ProjectModule.project_id == project_id).all()
    stories = db.query(UserStory).filter(UserStory.project_id == project_id).all()
    risks = db.query(ProjectRisk).filter(ProjectRisk.project_id == project_id).all()
    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    return {"project":{"id":project.id,"name":project.name,"description":project.description},
            "modules":[{"id":m.id,"name":m.name,"description":m.description,"story_count":m.story_count,"task_count":m.task_count,"risk_level":m.risk_level,"progress":m.progress} for m in modules],
            "user_stories":[{"id":s.id,"code":s.code,"module_id":s.module_id,"title":s.title,"description":s.description,"priority":s.priority,"points":s.points} for s in stories],
            "tasks":[TaskResponse.model_validate(t).model_dump() for t in tasks],
            "risks":[{"id":r.id,"code":r.code,"title":r.title,"description":r.description,"probability":r.probability,"impact":r.impact,"level":r.level,"recommendation":r.recommendation} for r in risks]}


@app.post("/demo/seed")
def seed_demo_data(db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.name == "Online Food Delivery AI").first()
    if not project:
        project = Project(name="Online Food Delivery AI", description="End-to-end AI-assisted food delivery software project", status="In Development")
        db.add(project); db.commit(); db.refresh(project)
    if db.query(TeamMember).filter(TeamMember.project_id == project.id).count() == 0:
        for name,role,skills,workload in [("Rahul Kumar","Backend Developer","Python, FastAPI, SQL, APIs",62),("Ananya Sharma","Frontend Developer","HTML, CSS, JavaScript, React, UI/UX",48),("Sam Teja","QA Engineer","Testing, pytest, automation, security",55),("Priya Reddy","AI Engineer","Python, ML, NLP, analytics",38)]:
            db.add(TeamMember(name=name,role=role,skills=skills,project_id=project.id,workload=workload))
        db.commit(); sync_members(db, project.id)
    if db.query(Task).filter(Task.project_id == project.id).count() == 0:
        tasks=[("Design authentication flow","High","Rahul Kumar",5,"in_progress"),("Build restaurant search API","High","Rahul Kumar",8,"todo"),("Create responsive food catalog","Medium","Ananya Sharma",5,"done"),("Implement cart and checkout","High","Ananya Sharma",8,"testing"),("Write payment integration tests","High","Sam Teja",5,"todo"),("Create recommendation prototype","Medium","Priya Reddy",8,"in_progress")]
        for title,priority,assignee,points,status in tasks: db.add(Task(title=title,description="Demo project task",priority=priority,status=status,assignee=assignee,points=points,project_id=project.id))
        db.commit(); sync_project(db, project.id)
    if db.query(Bug).filter(Bug.project_id == project.id).count() == 0:
        db.add(Bug(project_id=project.id,title="Payment callback intermittently fails",description="Payment succeeds but callback status is delayed",severity="High",status="Open",probable_area="Payments / API",root_cause="External callback timeout or retry handling")); db.commit()
        project.bugs=1; db.commit()
    log_activity(db, project.id, "seeded", "demo", "Demo project data is ready")
    return {"message":"Demo data ready", "project_id":project.id, "project_name":project.name}
@app.post("/demo/portfolio-seed")
def seed_portfolio_demo_data(db: Session = Depends(get_db)):
    """Create one-time illustrative task, team and bug data for existing projects.

    Existing projects are retained. Existing tasks have their statuses and
    assignees updated to create a varied demonstration portfolio. Existing
    team members and bugs are preserved. The Activity marker prevents reruns.
    """
    projects = db.query(Project).order_by(Project.id.desc()).all()
    if not projects:
        raise HTTPException(status_code=404, detail="No projects found")

    progress_targets = [10, 25, 40, 55, 70, 85, 95, 15, 30, 45, 60, 75, 90, 20, 35, 50, 65]
    team_templates = [
        ("Rahul Kumar", "Backend Developer", "Python, FastAPI, SQL, APIs", 62),
        ("Ananya Sharma", "Frontend Developer", "HTML, CSS, JavaScript, React", 48),
        ("Sam Teja", "QA Engineer", "Testing, pytest, automation", 55),
        ("Priya Reddy", "AI Engineer", "Python, Machine Learning, NLP", 38),
        ("Arjun Varma", "UI/UX Designer", "Figma, UI Design, Prototyping", 72),
    ]
    task_templates = [
        ("Design application architecture", "High", 8),
        ("Create user authentication", "High", 5),
        ("Develop responsive user interface", "Medium", 5),
        ("Build backend API endpoints", "High", 8),
        ("Configure database models", "High", 5),
        ("Implement core business logic", "Medium", 8),
        ("Integrate frontend and backend", "High", 8),
        ("Perform functional testing", "Medium", 5),
        ("Fix reported defects", "High", 3),
        ("Prepare deployment configuration", "Medium", 5),
        ("Review application security", "High", 5),
        ("Prepare project documentation", "Low", 2),
    ]
    bug_templates = [
        ("API response delay", "An application endpoint responds slower than expected.", "High", "Backend API", "Query optimization or inefficient processing"),
        ("Responsive layout issue", "Some interface elements do not align correctly on smaller screens.", "Medium", "Frontend UI", "Responsive layout or CSS breakpoint issue"),
        ("Input validation defect", "Invalid input is not handled correctly in a form.", "Low", "Input Validation", "Missing validation rule"),
    ]

    results = []
    for index, project in enumerate(projects):
        marker_exists = db.query(Activity).filter(
            Activity.project_id == project.id,
            Activity.entity_type == "portfolio_demo_seed_v1"
        ).first()
        if marker_exists:
            results.append({"project_id": project.id, "project": project.name, "result": "Already initialized"})
            continue

        tasks = db.query(Task).filter(Task.project_id == project.id).order_by(Task.id.asc()).all()
        if not tasks:
            for title, priority, points in task_templates:
                db.add(Task(
                    title=title,
                    description="Illustrative portfolio demonstration task.",
                    priority=priority,
                    status="todo",
                    assignee="PM",
                    points=points,
                    project_id=project.id,
                ))
            db.flush()
            tasks = db.query(Task).filter(Task.project_id == project.id).order_by(Task.id.asc()).all()

        members = db.query(TeamMember).filter(TeamMember.project_id == project.id).order_by(TeamMember.id.asc()).all()
        if not members:
            target_members = 2 + (index % 4)
            for name, role, skills, workload in team_templates[:target_members]:
                db.add(TeamMember(
                    name=name,
                    role=role,
                    skills=skills,
                    project_id=project.id,
                    workload=min(95, workload + (index % 3) * 5),
                ))
            db.flush()
            members = db.query(TeamMember).filter(
                TeamMember.project_id == project.id,
                TeamMember.active == True,
            ).order_by(TeamMember.id.asc()).all()

        target_progress = progress_targets[index % len(progress_targets)]
        total_tasks = len(tasks)
        completed_count = round(total_tasks * target_progress / 100)
        in_progress_count = min(2 + (index % 3), total_tasks - completed_count)
        remaining = total_tasks - completed_count - in_progress_count
        testing_count = min(index % 3, remaining)

        for task_index, task in enumerate(tasks):
            if task_index < completed_count:
                task.status = "done"
            elif task_index < completed_count + in_progress_count:
                task.status = "in_progress"
            elif task_index < completed_count + in_progress_count + testing_count:
                task.status = "testing"
            else:
                task.status = "todo"
            if members:
                task.assignee = members[task_index % len(members)].name

        existing_bugs = db.query(Bug).filter(Bug.project_id == project.id).count()
        target_bugs = index % 4
        if existing_bugs == 0:
            for bug_index in range(target_bugs):
                title, description, severity, area, cause = bug_templates[bug_index % len(bug_templates)]
                db.add(Bug(
                    project_id=project.id,
                    title=title,
                    description=description,
                    severity=severity,
                    status="Open",
                    probable_area=area,
                    root_cause=cause,
                ))

        db.flush()
        project_tasks = db.query(Task).filter(Task.project_id == project.id).all()
        active_members = db.query(TeamMember).filter(
            TeamMember.project_id == project.id,
            TeamMember.active == True,
        ).count()
        open_bugs = db.query(Bug).filter(
            Bug.project_id == project.id,
            Bug.status.notin_(["Resolved", "Closed", "Fixed"]),
        ).count()
        completed = sum(1 for task in project_tasks if task.status == "done")

        project.tasks = len(project_tasks)
        project.members = active_members
        project.bugs = open_bugs
        project.progress = round(completed / len(project_tasks) * 100, 1) if project_tasks else 0
        if project.progress >= 100:
            project.status = "Completed"
        elif project.progress >= 70:
            project.status = "Testing"
        elif project.progress > 0:
            project.status = "In Development"
        else:
            project.status = "Planning"

        db.add(Activity(
            project_id=project.id,
            action="demo_initialized",
            entity_type="portfolio_demo_seed_v1",
            message="Illustrative portfolio demonstration data initialized.",
        ))
        results.append({
            "project_id": project.id,
            "project": project.name,
            "tasks": project.tasks,
            "progress": project.progress,
            "members": project.members,
            "open_bugs": project.bugs,
            "status": project.status,
            "result": "Initialized",
        })

    db.commit()
    return {
        "message": "Portfolio demonstration data initialized",
        "projects_processed": len(projects),
        "results": results,
        "note": "Illustrative demo data; existing task statuses and assignees were updated. Existing team members and bugs were preserved.",
    }


# ============================================================
# PROJECT PULSE AI — PHASE 2 INTELLIGENCE ENGINE
# ============================================================
#
# Features:
# 1. AI Task Priority
# 2. AI Team Assignment
# 3. AI Risk Monitor
# 4. AI Project Health 2.0
# 5. AI Recommendations
# 6. Advanced Analytics
# 7. One-click AI Intelligence Scan
#
# This layer uses deterministic local intelligence.
# It can later be connected to an actual LLM/API.
# ============================================================


# ============================================================
# PHASE 2 — REQUEST MODELS
# ============================================================

class AITaskPriorityRequest(BaseModel):
    task_id: int


class AITeamAssignmentRequest(BaseModel):
    task_id: int


class AIIntelligenceRequest(BaseModel):
    project_id: int | None = None


# ============================================================
# PHASE 2 — HELPER FUNCTIONS
# ============================================================

def _safe_text(value):
    return str(value or "").strip().lower()


def _skill_list(member):
    return [
        item.strip().lower()
        for item in str(member.skills or "").split(",")
        if item.strip()
    ]


def _task_keywords(task):
    text = f"{task.title or ''} {task.description or ''}".lower()

    keyword_map = {
        "python": ["python", "fastapi", "django", "flask"],
        "javascript": ["javascript", "js", "frontend", "react", "vue", "angular"],
        "react": ["react", "frontend", "ui", "interface"],
        "backend": ["backend", "api", "server", "endpoint"],
        "api": ["api", "endpoint", "rest", "integration"],
        "database": ["database", "sql", "sqlite", "mysql", "postgres", "oracle"],
        "sql": ["sql", "database", "query"],
        "ai": ["ai", "machine learning", "ml", "model", "prediction"],
        "machine learning": ["machine learning", "ml", "prediction", "classification"],
        "testing": ["test", "testing", "qa", "quality", "validation"],
        "security": ["security", "authentication", "authorization", "password", "token"],
        "payment": ["payment", "razorpay", "stripe", "transaction"],
        "ui": ["ui", "design", "interface", "frontend"],
        "analytics": ["analytics", "dashboard", "chart", "visualization"],
        "devops": ["docker", "deployment", "devops", "ci/cd", "server"],
    }

    required = []

    for skill, words in keyword_map.items():
        if any(word in text for word in words):
            required.append(skill)

    if not required:
        required.append("general development")

    return required


def _role_match_score(task, member):
    task_text = f"{task.title} {task.description}".lower()
    role = str(member.role or "").lower()

    score = 0

    if "backend" in role and any(
        x in task_text for x in ["api", "backend", "server", "database", "payment"]
    ):
        score += 30

    if "frontend" in role and any(
        x in task_text for x in ["frontend", "ui", "interface", "react", "dashboard"]
    ):
        score += 30

    if "ai" in role and any(
        x in task_text for x in ["ai", "machine learning", "ml", "prediction", "model"]
    ):
        score += 35

    if "qa" in role or "tester" in role:
        if any(x in task_text for x in ["test", "testing", "quality", "validation"]):
            score += 35

    if "devops" in role:
        if any(x in task_text for x in ["deploy", "docker", "server", "devops", "ci/cd"]):
            score += 30

    if "full stack" in role:
        score += 15

    if "developer" in role:
        score += 5

    return score


def _skill_match_score(task, member):
    required = _task_keywords(task)
    skills = _skill_list(member)

    score = 0
    matched = []

    for required_skill in required:
        for member_skill in skills:
            if (
                required_skill in member_skill
                or member_skill in required_skill
                or required_skill == "general development"
            ):
                score += 20
                matched.append(member_skill)
                break

    return score, matched


def _workload_score(member):
    workload = max(0, min(100, int(member.workload or 0)))

    if workload <= 25:
        return 30

    if workload <= 50:
        return 24

    if workload <= 70:
        return 15

    if workload <= 85:
        return 5

    return -10


def _priority_analysis(task):
    text = f"{task.title or ''} {task.description or ''}".lower()

    score = 0
    reasons = []

    high_keywords = [
        "payment",
        "security",
        "authentication",
        "login",
        "database",
        "production",
        "critical",
        "bug",
        "crash",
        "failure",
        "deployment",
        "release",
        "api",
    ]

    medium_keywords = [
        "dashboard",
        "analytics",
        "notification",
        "search",
        "profile",
        "report",
        "ui",
    ]

    for keyword in high_keywords:
        if keyword in text:
            score += 15
            reasons.append(f"Contains critical keyword: {keyword}")

    for keyword in medium_keywords:
        if keyword in text:
            score += 5
            reasons.append(f"Contains important feature keyword: {keyword}")

    points = int(task.points or 1)

    if points >= 8:
        score += 20
        reasons.append("High story-point estimate")

    elif points >= 5:
        score += 10
        reasons.append("Moderate implementation effort")

    if task.status in {"in_progress", "testing"}:
        score += 5
        reasons.append("Task is already active")

    if not reasons:
        reasons.append("Standard development task with no critical indicators")

    if score >= 35:
        priority = "High"
        risk = "High"
    elif score >= 15:
        priority = "Medium"
        risk = "Medium"
    else:
        priority = "Low"
        risk = "Low"

    return {
        "priority": priority,
        "risk": risk,
        "score": min(score, 100),
        "reasons": reasons[:5],
        "ai_reason": " ".join(reasons[:3])
    }


def _risk_level(probability, impact):
    impact_value = str(impact).lower()

    if probability >= 70 or impact_value == "high":
        return "HIGH"

    if probability >= 40 or impact_value == "medium":
        return "MEDIUM"

    return "LOW"


def _generate_project_recommendations(
    project,
    tasks,
    bugs,
    members,
    risks
):
    recommendations = []

    total_tasks = len(tasks)
    completed = sum(1 for t in tasks if t.status == "done")
    active = sum(
        1 for t in tasks
        if t.status in {"in_progress", "testing"}
    )

    open_bugs = [
        b for b in bugs
        if str(b.status).lower() not in {"resolved", "closed"}
    ]

    high_bugs = [
        b for b in open_bugs
        if str(b.severity).lower() in {"high", "critical"}
    ]

    overloaded = [
        m for m in members
        if int(m.workload or 0) >= 80
    ]

    unassigned = [
        t for t in tasks
        if not t.assignee
        or str(t.assignee).lower() in {
            "pm",
            "ai recommended",
            "unassigned"
        }
    ]

    high_tasks = [
        t for t in tasks
        if str(t.priority).lower() == "high"
    ]

    if high_bugs:
        recommendations.append({
            "type": "Bug",
            "priority": "High",
            "title": "Resolve high-severity bugs",
            "message": f"{len(high_bugs)} high-severity open bug(s) require attention before release.",
            "action": "Open Bugs and prioritize resolution."
        })

    if overloaded:
        names = ", ".join(m.name for m in overloaded[:3])

        recommendations.append({
            "type": "Team",
            "priority": "High",
            "title": "Balance team workload",
            "message": f"{names} currently have high workload.",
            "action": "Use AI Team Assignment to redistribute work."
        })

    if unassigned:
        recommendations.append({
            "type": "Assignment",
            "priority": "Medium",
            "title": "Assign unassigned tasks",
            "message": f"{len(unassigned)} task(s) need an owner.",
            "action": "Run AI Team Assignment."
        })

    if high_tasks:
        recommendations.append({
            "type": "Priority",
            "priority": "High",
            "title": "Review high-priority work",
            "message": f"{len(high_tasks)} task(s) are currently marked High priority.",
            "action": "Complete critical tasks before lower-priority work."
        })

    if total_tasks and completed / total_tasks < 0.35:
        recommendations.append({
            "type": "Progress",
            "priority": "Medium",
            "title": "Increase completion velocity",
            "message": "The project has a relatively low completion ratio.",
            "action": "Focus the team on finishing active tasks."
        })

    if total_tasks and active / total_tasks > 0.70:
        recommendations.append({
            "type": "Bottleneck",
            "priority": "High",
            "title": "Too many active tasks",
            "message": "A large percentage of tasks are currently in progress/testing.",
            "action": "Finish existing work before starting more tasks."
        })

    if risks:
        high_risks = [
            r for r in risks
            if str(r.level).upper() == "HIGH"
        ]

        if high_risks:
            recommendations.append({
                "type": "Risk",
                "priority": "High",
                "title": "Mitigate high project risks",
                "message": f"{len(high_risks)} high-level project risk(s) detected.",
                "action": "Review the Risk Monitor and mitigation recommendations."
            })

    if not recommendations:
        recommendations.append({
            "type": "General",
            "priority": "Low",
            "title": "Project operating normally",
            "message": "No major intelligence alerts were detected.",
            "action": "Continue monitoring progress, bugs, workload and risks."
        })

    return recommendations[:8]


# ============================================================
# 1. AI TASK PRIORITY
# ============================================================

@app.post("/ai/tasks/{task_id}/priority")
def ai_task_priority(
    task_id: int,
    db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    analysis = _priority_analysis(task)

    task.priority = analysis["priority"]

    db.commit()
    db.refresh(task)

    if task.project_id:
        log_activity(
            db,
            task.project_id,
            "analyzed",
            "ai_priority",
            f"AI analyzed task priority: {task.title} → {task.priority}"
        )

    return {
        "task_id": task.id,
        "task": task.title,
        "priority": task.priority,
        "risk": analysis["risk"],
        "score": analysis["score"],
        "reasons": analysis["reasons"],
        "ai_reason": analysis["ai_reason"]
    }


# ============================================================
# 2. AI TEAM ASSIGNMENT
# ============================================================

@app.post("/ai/tasks/{task_id}/assign")
def ai_team_assignment(
    task_id: int,
    db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    if not task.project_id:
        raise HTTPException(
            status_code=400,
            detail="Task is not connected to a project"
        )

    members = (
        db.query(TeamMember)
        .filter(
            TeamMember.project_id == task.project_id,
            TeamMember.active == True
        )
        .all()
    )

    if not members:
        raise HTTPException(
            status_code=400,
            detail="No active team members found for this project"
        )

    candidates = []

    required_skills = _task_keywords(task)

    for member in members:
        skill_score, matched_skills = _skill_match_score(
            task,
            member
        )

        role_score = _role_match_score(
            task,
            member
        )

        workload_score = _workload_score(
            member
        )

        total_score = (
            skill_score +
            role_score +
            workload_score
        )

        candidates.append({
            "member": member,
            "score": total_score,
            "matched_skills": matched_skills
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    selected = candidates[0]
    member = selected["member"]

    old_assignee = task.assignee

    task.assignee = member.name

    workload_increase = max(
        3,
        min(15, int(task.points or 1) * 2)
    )

    member.workload = min(
        100,
        int(member.workload or 0) + workload_increase
    )

    db.commit()
    db.refresh(task)
    db.refresh(member)

    log_activity(
        db,
        task.project_id,
        "assigned",
        "ai_assignment",
        f"AI assigned '{task.title}' to {member.name}"
    )

    return {
        "task_id": task.id,
        "task": task.title,
        "previous_assignee": old_assignee,
        "recommended_assignee": member.name,
        "role": member.role,
        "skills": member.skills,
        "matched_skills": selected["matched_skills"],
        "required_skills": required_skills,
        "match_score": selected["score"],
        "new_workload": member.workload,
        "reason": (
            f"{member.name} was selected based on "
            f"role compatibility, relevant skills and workload."
        ),
        "alternatives": [
            {
                "name": item["member"].name,
                "role": item["member"].role,
                "score": item["score"]
            }
            for item in candidates[1:4]
        ]
    }


# ============================================================
# 3. AI TASK INTELLIGENCE
# ============================================================

@app.get("/ai/tasks/{task_id}/intelligence")
def ai_task_intelligence(
    task_id: int,
    db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    priority = _priority_analysis(task)

    return {
        "task_id": task.id,
        "title": task.title,
        "current_priority": task.priority,
        "recommended_priority": priority["priority"],
        "risk": priority["risk"],
        "score": priority["score"],
        "reason": priority["ai_reason"],
        "required_skills": _task_keywords(task),
        "status": task.status,
        "assignee": task.assignee,
        "points": task.points
    }


# ============================================================
# 4. AI RISK MONITOR
# ============================================================

@app.get("/ai/projects/{project_id}/risks")
def ai_project_risks(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    bugs = db.query(Bug).filter(
        Bug.project_id == project_id
    ).all()

    members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.active == True
    ).all()

    risks = db.query(ProjectRisk).filter(
        ProjectRisk.project_id == project_id
    ).all()

    intelligence = []

    # High-priority task concentration
    high_tasks = [
        t for t in tasks
        if str(t.priority).lower() == "high"
    ]

    if high_tasks:
        probability = min(
            95,
            30 + len(high_tasks) * 8
        )

        intelligence.append({
            "code": "TASK-001",
            "title": "High-priority task concentration",
            "description": f"{len(high_tasks)} high-priority task(s) require attention.",
            "probability": probability,
            "impact": "High" if len(high_tasks) >= 4 else "Medium",
            "level": _risk_level(
                probability,
                "High" if len(high_tasks) >= 4 else "Medium"
            ),
            "recommendation": "Prioritize critical tasks and reduce work-in-progress."
        })

    # Open bugs
    open_bugs = [
        b for b in bugs
        if str(b.status).lower() not in {"resolved", "closed"}
    ]

    if open_bugs:
        probability = min(
            95,
            25 + len(open_bugs) * 10
        )

        intelligence.append({
            "code": "BUG-001",
            "title": "Open defect accumulation",
            "description": f"{len(open_bugs)} unresolved bug(s) detected.",
            "probability": probability,
            "impact": "High" if len(open_bugs) >= 3 else "Medium",
            "level": _risk_level(
                probability,
                "High" if len(open_bugs) >= 3 else "Medium"
            ),
            "recommendation": "Resolve high-severity bugs before adding non-critical features."
        })

    # Workload risk
    overloaded = [
        m for m in members
        if int(m.workload or 0) >= 80
    ]

    if overloaded:
        probability = min(
            95,
            40 + len(overloaded) * 10
        )

        intelligence.append({
            "code": "TEAM-001",
            "title": "Team workload imbalance",
            "description": f"{len(overloaded)} team member(s) have workload ≥ 80%.",
            "probability": probability,
            "impact": "Medium",
            "level": _risk_level(
                probability,
                "Medium"
            ),
            "recommendation": "Redistribute tasks using AI Team Assignment."
        })

    # Too many active tasks
    total_tasks = len(tasks)

    active_tasks = [
        t for t in tasks
        if t.status in {"in_progress", "testing"}
    ]

    if total_tasks and len(active_tasks) / total_tasks > 0.70:
        probability = 75

        intelligence.append({
            "code": "FLOW-001",
            "title": "Work-in-progress bottleneck",
            "description": "More than 70% of tasks are currently active.",
            "probability": probability,
            "impact": "High",
            "level": "HIGH",
            "recommendation": "Finish existing tasks before starting additional work."
        })

    # Database-generated project risks
    for risk in risks:
        intelligence.append({
            "code": risk.code,
            "title": risk.title,
            "description": risk.description,
            "probability": risk.probability,
            "impact": risk.impact,
            "level": risk.level,
            "recommendation": risk.recommendation
        })

    if not intelligence:
        intelligence.append({
            "code": "SYS-001",
            "title": "No major risk detected",
            "description": "Current project indicators are within normal thresholds.",
            "probability": 10,
            "impact": "Low",
            "level": "LOW",
            "recommendation": "Continue monitoring project indicators."
        })

    high_count = sum(
        1 for r in intelligence
        if str(r["level"]).upper() == "HIGH"
    )

    medium_count = sum(
        1 for r in intelligence
        if str(r["level"]).upper() == "MEDIUM"
    )

    low_count = sum(
        1 for r in intelligence
        if str(r["level"]).upper() == "LOW"
    )

    return {
        "project_id": project_id,
        "project": project.name,
        "total_risks": len(intelligence),
        "high": high_count,
        "medium": medium_count,
        "low": low_count,
        "risks": intelligence
    }


# ============================================================
# 5. AI PROJECT HEALTH 2.0
# ============================================================

@app.get("/ai/projects/{project_id}/health")
def ai_project_health(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    bugs = db.query(Bug).filter(
        Bug.project_id == project_id
    ).all()

    members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.active == True
    ).all()

    risks = db.query(ProjectRisk).filter(
        ProjectRisk.project_id == project_id
    ).all()

    total = len(tasks)

    completed = sum(
        1 for t in tasks
        if t.status == "done"
    )

    active = sum(
        1 for t in tasks
        if t.status in {"in_progress", "testing"}
    )

    todo = sum(
        1 for t in tasks
        if t.status == "todo"
    )

    open_bugs = [
        b for b in bugs
        if str(b.status).lower() not in {"resolved", "closed"}
    ]

    critical_bugs = [
        b for b in open_bugs
        if str(b.severity).lower() in {"critical", "high"}
    ]

    high_tasks = [
        t for t in tasks
        if str(t.priority).lower() == "high"
    ]

    overloaded = [
        m for m in members
        if int(m.workload or 0) >= 80
    ]

    # -----------------------------
    # Individual health dimensions
    # -----------------------------

    progress_score = (
        round(completed / total * 100)
        if total else 0
    )

    quality_score = max(
        0,
        100 - len(open_bugs) * 10 - len(critical_bugs) * 10
    )

    workload_score = (
        round(
            sum(
                max(0, 100 - int(m.workload or 0))
                for m in members
            ) / len(members)
        )
        if members else 75
    )

    risk_score = max(
        0,
        100 - len(risks) * 12
    )

    execution_score = 100

    if total and active / total > 0.70:
        execution_score -= 25

    if total and todo / total > 0.70:
        execution_score -= 15

    execution_score = max(
        0,
        execution_score
    )

    # -----------------------------
    # Weighted health score
    # -----------------------------

    score = round(
        progress_score * 0.30 +
        quality_score * 0.25 +
        workload_score * 0.15 +
        risk_score * 0.15 +
        execution_score * 0.15
    )

    score = max(
        0,
        min(100, score)
    )

    if score >= 85:
        label = "Excellent"
    elif score >= 70:
        label = "Healthy"
    elif score >= 50:
        label = "Attention Needed"
    else:
        label = "Critical"

    # -----------------------------
    # AI explanation
    # -----------------------------

    positives = []
    concerns = []

    if progress_score >= 70:
        positives.append("good task completion progress")

    if quality_score >= 80:
        positives.append("controlled defect levels")

    if workload_score >= 70:
        positives.append("manageable team workload")

    if risk_score >= 80:
        positives.append("limited project risk exposure")

    if active and total and active / total > 0.70:
        concerns.append("high work-in-progress")

    if critical_bugs:
        concerns.append("high-severity open bugs")

    if overloaded:
        concerns.append("team workload concentration")

    if risks:
        concerns.append("active project risks")

    explanation_parts = []

    if positives:
        explanation_parts.append(
            "Positive indicators: " +
            ", ".join(positives) +
            "."
        )

    if concerns:
        explanation_parts.append(
            "Attention areas: " +
            ", ".join(concerns) +
            "."
        )

    if not explanation_parts:
        explanation_parts.append(
            "AI monitoring found no major project health signal."
        )

    return {
        "project_id": project_id,
        "project": project.name,
        "score": score,
        "label": label,
        "dimensions": {
            "progress": progress_score,
            "quality": quality_score,
            "workload": workload_score,
            "risk": risk_score,
            "execution": execution_score
        },
        "metrics": {
            "total_tasks": total,
            "completed_tasks": completed,
            "active_tasks": active,
            "todo_tasks": todo,
            "open_bugs": len(open_bugs),
            "critical_bugs": len(critical_bugs),
            "high_priority_tasks": len(high_tasks),
            "overloaded_members": len(overloaded),
            "project_risks": len(risks)
        },
        "ai_explanation": " ".join(explanation_parts)
    }


# ============================================================
# 6. AI RECOMMENDATIONS
# ============================================================

@app.get("/ai/projects/{project_id}/recommendations")
def ai_project_recommendations(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    bugs = db.query(Bug).filter(
        Bug.project_id == project_id
    ).all()

    members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.active == True
    ).all()

    risks = db.query(ProjectRisk).filter(
        ProjectRisk.project_id == project_id
    ).all()

    recommendations = _generate_project_recommendations(
        project,
        tasks,
        bugs,
        members,
        risks
    )

    return {
        "project_id": project_id,
        "project": project.name,
        "count": len(recommendations),
        "recommendations": recommendations
    }


# ============================================================
# 7. ADVANCED ANALYTICS
# ============================================================

@app.get("/ai/projects/{project_id}/analytics")
def ai_project_analytics(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    bugs = db.query(Bug).filter(
        Bug.project_id == project_id
    ).all()

    members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.active == True
    ).all()

    risks = db.query(ProjectRisk).filter(
        ProjectRisk.project_id == project_id
    ).all()

    status_distribution = {
        "todo": sum(
            1 for t in tasks
            if t.status == "todo"
        ),
        "in_progress": sum(
            1 for t in tasks
            if t.status == "in_progress"
        ),
        "testing": sum(
            1 for t in tasks
            if t.status == "testing"
        ),
        "done": sum(
            1 for t in tasks
            if t.status == "done"
        )
    }

    priority_distribution = {
        "high": sum(
            1 for t in tasks
            if str(t.priority).lower() == "high"
        ),
        "medium": sum(
            1 for t in tasks
            if str(t.priority).lower() == "medium"
        ),
        "low": sum(
            1 for t in tasks
            if str(t.priority).lower() == "low"
        )
    }

    bug_distribution = {
        "critical": sum(
            1 for b in bugs
            if str(b.severity).lower() == "critical"
        ),
        "high": sum(
            1 for b in bugs
            if str(b.severity).lower() == "high"
        ),
        "medium": sum(
            1 for b in bugs
            if str(b.severity).lower() == "medium"
        ),
        "low": sum(
            1 for b in bugs
            if str(b.severity).lower() == "low"
        )
    }

    risk_distribution = {
        "high": sum(
            1 for r in risks
            if str(r.level).upper() == "HIGH"
        ),
        "medium": sum(
            1 for r in risks
            if str(r.level).upper() == "MEDIUM"
        ),
        "low": sum(
            1 for r in risks
            if str(r.level).upper() == "LOW"
        )
    }

    workload = [
        {
            "id": m.id,
            "name": m.name,
            "role": m.role,
            "workload": int(m.workload or 0),
            "skills": m.skills,
            "active": m.active
        }
        for m in members
    ]

    assignee_distribution = {}

    for task in tasks:
        name = task.assignee or "Unassigned"

        assignee_distribution[name] = (
            assignee_distribution.get(name, 0) + 1
        )

    total_points = sum(
        int(t.points or 0)
        for t in tasks
    )

    completed_points = sum(
        int(t.points or 0)
        for t in tasks
        if t.status == "done"
    )

    return {
        "project_id": project_id,
        "project": project.name,
        "summary": {
            "tasks": len(tasks),
            "story_points": total_points,
            "completed_story_points": completed_points,
            "members": len(members),
            "bugs": len(bugs),
            "risks": len(risks)
        },
        "status_distribution": status_distribution,
        "priority_distribution": priority_distribution,
        "bug_distribution": bug_distribution,
        "risk_distribution": risk_distribution,
        "team_workload": workload,
        "assignee_distribution": assignee_distribution
    }


# ============================================================
# 8. ONE-CLICK AI INTELLIGENCE SCAN
# ============================================================

@app.post("/ai/projects/{project_id}/intelligence-scan")
def ai_intelligence_scan(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    # -----------------------------
    # Analyze task priorities
    # -----------------------------

    priority_results = []

    for task in tasks:
        analysis = _priority_analysis(task)

        old_priority = task.priority

        task.priority = analysis["priority"]

        priority_results.append({
            "task_id": task.id,
            "title": task.title,
            "old_priority": old_priority,
            "new_priority": task.priority,
            "risk": analysis["risk"],
            "score": analysis["score"],
            "reason": analysis["ai_reason"]
        })

    db.commit()

    # -----------------------------
    # Generate health/risk/recs
    # -----------------------------

    health = ai_project_health(
        project_id,
        db
    )

    risks = ai_project_risks(
        project_id,
        db
    )

    recommendations = ai_project_recommendations(
        project_id,
        db
    )

    log_activity(
        db,
        project_id,
        "scanned",
        "ai_intelligence",
        f"AI Intelligence Scan analyzed {len(tasks)} task(s)"
    )

    return {
        "project": {
            "id": project.id,
            "name": project.name
        },
        "tasks_analyzed": len(tasks),
        "priority_analysis": priority_results,
        "health": health,
        "risks": risks,
        "recommendations": recommendations,
        "ai_status": "completed",
        "message": "AI Intelligence Scan completed successfully."
    }


# ============================================================
# 9. AI ASSIGN ALL UNASSIGNED / AI-RECOMMENDED TASKS
# ============================================================

@app.post("/ai/projects/{project_id}/assign-all")
def ai_assign_all_tasks(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    members = db.query(TeamMember).filter(
        TeamMember.project_id == project_id,
        TeamMember.active == True
    ).all()

    if not members:
        raise HTTPException(
            status_code=400,
            detail="No active team members found"
        )

    assigned = []

    for task in tasks:

        candidates = []

        for member in members:

            skill_score, matched_skills = _skill_match_score(
                task,
                member
            )

            role_score = _role_match_score(
                task,
                member
            )

            workload_score = _workload_score(
                member
            )

            score = (
                skill_score +
                role_score +
                workload_score
            )

            candidates.append({
                "member": member,
                "score": score,
                "matched_skills": matched_skills
            })

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        selected = candidates[0]
        member = selected["member"]

        task.assignee = member.name

        member.workload = min(
            100,
            int(member.workload or 0)
            + max(3, min(12, int(task.points or 1) * 2))
        )

        assigned.append({
            "task_id": task.id,
            "task": task.title,
            "assignee": member.name,
            "role": member.role,
            "match_score": selected["score"],
            "matched_skills": selected["matched_skills"]
        })

    db.commit()

    log_activity(
        db,
        project_id,
        "assigned",
        "ai_assignment",
        f"AI assigned {len(assigned)} project task(s)"
    )

    return {
        "project_id": project_id,
        "assigned_count": len(assigned),
        "assignments": assigned,
        "message": "AI team assignment completed."
    }


# ============================================================
# 10. AI PROJECT COMMAND CENTER
# ============================================================

@app.get("/ai/projects/{project_id}/command-center")
def ai_command_center(
    project_id: int,
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    health = ai_project_health(
        project_id,
        db
    )

    risks = ai_project_risks(
        project_id,
        db
    )

    recommendations = ai_project_recommendations(
        project_id,
        db
    )

    analytics = ai_project_analytics(
        project_id,
        db
    )

    tasks = db.query(Task).filter(
        Task.project_id == project_id
    ).all()

    top_tasks = []

    for task in tasks:
        analysis = _priority_analysis(task)

        top_tasks.append({
            "id": task.id,
            "title": task.title,
            "priority": task.priority,
            "recommended_priority": analysis["priority"],
            "risk": analysis["risk"],
            "score": analysis["score"],
            "assignee": task.assignee,
            "status": task.status
        })

    top_tasks.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "status": project.status,
            "progress": project.progress
        },
        "health": health,
        "risks": risks,
        "recommendations": recommendations,
        "analytics": analytics,
        "priority_tasks": top_tasks[:10],
        "ai_engine": {
            "status": "active",
            "modules": [
                "Task Priority",
                "Team Assignment",
                "Risk Monitor",
                "Project Health",
                "Recommendations",
                "Analytics"
            ]
        }
    }