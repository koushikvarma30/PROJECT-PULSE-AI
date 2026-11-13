from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey, Boolean, DateTime
from datetime import datetime
from database import Base

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    description = Column(Text, default="")
    status = Column(String(30), default="Planning")
    progress = Column(Float, default=0)
    tasks = Column(Integer, default=0)
    members = Column(Integer, default=0)
    bugs = Column(Integer, default=0)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(30), default="Medium")
    status = Column(String(30), default="todo")
    assignee = Column(String(100), default="PM")
    points = Column(Integer, default=1)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)

class Requirement(Base):
    __tablename__ = "requirements"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(40), default="Draft")
    complexity = Column(String(30), default="Medium")
    priority = Column(String(30), default="Medium")
    analysis_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)

class TeamMember(Base):
    __tablename__ = "team_members"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    role = Column(String(100), default="Developer")
    skills = Column(Text, default="")
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    workload = Column(Integer, default=0)
    active = Column(Boolean, default=True)

class TeamProject(Base):
    __tablename__ = "team_projects"

    team_member_id = Column(
        Integer,
        ForeignKey("team_members.id"),
        primary_key=True
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id"),
        primary_key=True
    )

    active = Column(Boolean, default=True)

class Bug(Base):
    __tablename__ = "bugs"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    severity = Column(String(30), default="Medium")
    status = Column(String(30), default="Open")
    probable_area = Column(String(150), default="Application Service")
    root_cause = Column(Text, default="Business logic or integration failure")
    created_at = Column(DateTime, default=datetime.utcnow)

class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    action = Column(String(50), default="updated")
    entity_type = Column(String(50), default="system")
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProjectModule(Base):
    __tablename__ = "project_modules"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    name = Column(String(150), nullable=False)
    description = Column(Text, default="")
    story_count = Column(Integer, default=0)
    task_count = Column(Integer, default=0)
    risk_level = Column(String(30), default="Low")
    progress = Column(Float, default=0)

class UserStory(Base):
    __tablename__ = "user_stories"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    module_id = Column(Integer, ForeignKey("project_modules.id"), nullable=True)
    code = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(30), default="Medium")
    points = Column(Integer, default=5)

class ProjectRisk(Base):
    __tablename__ = "project_risks"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    code = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    probability = Column(Integer, default=50)
    impact = Column(String(30), default="Medium")
    level = Column(String(30), default="MEDIUM")
    recommendation = Column(Text, default="")


class AnalyticsRecord(Base):
    __tablename__ = "analytics_records"
    id = Column(Integer, primary_key=True, index=True)
    source_s_no = Column(Integer, index=True)
    employee_id = Column(String(40), index=True)
    name = Column(String(160), nullable=False)
    project = Column(String(160), index=True)
    role = Column(String(120), index=True)
    skills = Column(Text, default="")
    experience_years = Column(Float, default=0)
    projects_handled = Column(Integer, default=0)
    rating = Column(Float, default=0)
    task_completion = Column(Float, default=0)
    availability_status = Column(String(80), index=True)
    location = Column(String(120), index=True)
    education = Column(String(120))
    monthly_cost_inr = Column(Float, default=0)
    date_joined = Column(String(20), index=True)
    team_size = Column(Integer, default=0)
    bugs_resolved = Column(Integer, default=0)
    on_time_delivery = Column(Float, default=0)
