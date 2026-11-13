
from database import SessionLocal
from models import (
    Project, Task, TeamMember, Bug, Activity,
    Requirement, ProjectModule, UserStory, ProjectRisk
)

db = SessionLocal()

projects_data = [
    ("Smart Food Delivery", "Online food ordering and delivery platform"),
    ("Hospital Management", "Hospital records and appointment system"),
    ("AI Resume Builder", "AI-powered resume creation platform"),
    ("E-Learning Platform", "Online courses and student management"),
    ("Banking Management", "Digital banking management system"),
    ("Smart Agriculture", "Agriculture monitoring and prediction"),
    ("Online Shopping", "E-commerce shopping platform"),
    ("Travel Booking", "Travel and hotel reservation system"),
    ("Fitness Tracker", "Personal fitness monitoring application"),
    ("Library Management", "Digital library management"),
    ("Student Portal", "College student information portal"),
    ("Smart Parking", "Automated parking management"),
    ("Healthcare AI", "AI-based healthcare assistance"),
    ("Job Recruitment", "Job search and recruitment platform"),
    ("Inventory System", "Business inventory management"),
    ("Expense Tracker", "Personal finance tracking application"),
    ("Weather Forecast", "Weather prediction dashboard"),
    ("Chat Application", "Real-time messaging platform"),
    ("Cybersecurity Monitor", "Security monitoring platform"),
    ("Online Examination", "Digital examination management"),
    ("Smart Transport", "Public transport tracking system"),
    ("Real Estate Portal", "Property listing and management"),
    ("Event Management", "Event planning and registration"),
    ("Hotel Management", "Hotel booking and operations"),
    ("AI Chatbot", "Intelligent customer support chatbot"),
    ("Sales Dashboard", "Sales tracking and reporting"),
    ("Water Management", "Smart water usage monitoring"),
    ("Vehicle Service", "Vehicle maintenance booking system"),
    ("Digital Payments", "Secure online payment platform"),
    ("Project Collaboration", "Team collaboration workspace")
]

members_data = [
    ("Aarav Sharma", "Project Manager", "Planning, Agile, Leadership"),
    ("Priya Reddy", "Python Developer", "Python, FastAPI, SQL"),
    ("Rahul Verma", "Frontend Developer", "HTML, CSS, JavaScript"),
    ("Sneha Rao", "Data Analyst", "Python, Pandas, Power BI"),
    ("Arjun Kumar", "AI Engineer", "Machine Learning, Python"),
    ("Ananya Singh", "UI/UX Designer", "Figma, UI Design"),
    ("Vikram Patel", "Backend Developer", "Python, APIs, SQL"),
    ("Kavya Nair", "Tester", "Manual Testing, Selenium"),
    ("Rohan Das", "DevOps Engineer", "Docker, Linux, Cloud"),
    ("Meera Joshi", "Database Developer", "SQL, Database Design"),
    ("Aditya Rao", "Java Developer", "Java, Spring Boot"),
    ("Ishita Gupta", "Data Scientist", "Python, ML, Statistics"),
    ("Karan Mehta", "Security Engineer", "Cybersecurity, Networking"),
    ("Pooja Sharma", "Frontend Developer", "React, JavaScript"),
    ("Sanjay Kumar", "Cloud Engineer", "AWS, Azure, Docker"),
    ("Divya Reddy", "QA Engineer", "Testing, Automation"),
    ("Nikhil Verma", "Python Developer", "Python, Django"),
    ("Riya Kapoor", "UI/UX Designer", "Figma, Prototyping"),
    ("Manoj Singh", "Backend Developer", "Node.js, APIs"),
    ("Neha Patel", "Data Analyst", "Excel, SQL, Power BI"),
    ("Varun Kumar", "AI Engineer", "Deep Learning, NLP"),
    ("Sravya Rao", "Mobile Developer", "Flutter, Dart"),
    ("Akash Sharma", "DevOps Engineer", "CI/CD, Git, Linux"),
    ("Harini Nair", "Tester", "Selenium, API Testing"),
    ("Abhishek Das", "Database Developer", "MySQL, PostgreSQL"),
    ("Keerthi Reddy", "Java Developer", "Java, Spring Boot"),
    ("Sai Krishna", "Cloud Engineer", "AWS, Cloud Security"),
    ("Tanvi Gupta", "Frontend Developer", "React, CSS"),
    ("Ritesh Patel", "Backend Developer", "FastAPI, Python"),
    ("Lavanya Rao", "Project Coordinator", "Agile, Documentation")
]

task_templates = [
    ("Requirement Analysis", "Collect and document project requirements.", "High", "todo", 5),
    ("Database Design", "Design and validate the database structure.", "High", "in_progress", 8),
    ("Frontend Development", "Build the main application interface.", "Medium", "in_progress", 5),
    ("Backend API", "Develop and test backend API endpoints.", "High", "testing", 8),
    ("Authentication", "Implement secure user authentication.", "High", "done", 5),
    ("UI Improvements", "Improve layout and user experience.", "Low", "todo", 3),
    ("Integration Testing", "Test integration between application modules.", "Medium", "testing", 5),
    ("Deployment Setup", "Prepare application for deployment.", "High", "in_progress", 8),
    ("Documentation", "Prepare technical project documentation.", "Low", "done", 2),
    ("Performance Testing", "Measure and improve application performance.", "Medium", "todo", 5),
    ("Security Review", "Review application security and permissions.", "Critical", "in_progress", 8),
    ("Dashboard Development", "Develop project dashboard and visual reports.", "Medium", "done", 5),
    ("Bug Fixing", "Identify and resolve reported defects.", "High", "testing", 3),
    ("API Documentation", "Document API requests and responses.", "Low", "todo", 2),
    ("Data Validation", "Validate application data and inputs.", "Medium", "in_progress", 5),
    ("User Testing", "Collect feedback from test users.", "Medium", "done", 3),
    ("Cloud Configuration", "Configure cloud deployment environment.", "High", "todo", 5),
    ("Code Review", "Review code quality and maintainability.", "Medium", "testing", 3),
    ("Report Generation", "Generate project performance reports.", "Low", "done", 3),
    ("Final Integration", "Integrate all completed application modules.", "High", "in_progress", 8),
    ("Mobile Optimization", "Improve application responsiveness.", "Medium", "todo", 5),
    ("Search Functionality", "Implement search and filtering features.", "Medium", "in_progress", 5),
    ("Notification System", "Implement application notifications.", "Low", "done", 3),
    ("Backup Configuration", "Configure database backup procedures.", "High", "todo", 3),
    ("Machine Learning Model", "Train and evaluate the ML model.", "Critical", "in_progress", 8),
    ("Payment Integration", "Integrate secure payment processing.", "High", "testing", 8),
    ("Final Quality Check", "Perform final quality assurance checks.", "Medium", "done", 3),
    ("Release Preparation", "Prepare the application release.", "High", "todo", 5),
    ("System Monitoring", "Configure system monitoring and alerts.", "Medium", "in_progress", 3),
    ("Project Completion", "Complete final project verification.", "High", "done", 5)
]

project_statuses = [
    "Planning", "In Progress", "In Progress", "In Progress",
    "Completed", "On Hold", "Planning", "In Progress",
    "Completed", "In Progress", "Planning", "Completed",
    "In Progress", "In Progress", "Planning", "Completed",
    "On Hold", "In Progress", "Completed", "Planning",
    "In Progress", "Completed", "Planning", "In Progress",
    "In Progress", "Completed", "Planning", "In Progress",
    "On Hold", "Completed"
]

try:
    print("Clearing existing operational records...")

    for model in [
        UserStory, ProjectRisk, Activity, Bug, Requirement,
        Task, TeamMember, ProjectModule, Project
    ]:
        db.query(model).delete(synchronize_session=False)

    db.commit()

    print("Creating 30 projects...")

    projects = []
    for i, data in enumerate(projects_data):
        title, description = data
        task = task_templates[i]
        task_status = task[3]

        progress = {
            "todo": 0,
            "in_progress": 40,
            "testing": 75,
            "done": 100
        }[task_status]

        project = Project(
            name=title,
            description=description,
            status=project_statuses[i],
            progress=progress,
            tasks=1,
            members=1,
            bugs=0
        )
        db.add(project)
        projects.append(project)

    db.flush()

    print("Creating 30 team members...")

    members = []
    for i, data in enumerate(members_data):
        name, role, skills = data
        member = TeamMember(
            name=name,
            role=role,
            skills=skills,
            project_id=projects[i].id,
            workload=25 + ((i * 17) % 71),
            active=True
        )
        db.add(member)
        members.append(member)

    db.flush()

    print("Creating 30 tasks...")

    for i, task_data in enumerate(task_templates):
        title, description, priority, status, points = task_data

        db.add(Task(
            title=title,
            description=description,
            priority=priority,
            status=status,
            assignee=members[i].name,
            points=points,
            project_id=projects[i].id
        ))

    db.commit()

    print("\nDATASET CREATED SUCCESSFULLY")
    print("Projects: 30")
    print("Team Members: 30")
    print("Tasks: 30")
    print("Bugs: 0")
    print("Imported analytics_records: preserved")

except Exception as error:
    db.rollback()
    print("ERROR:", error)
    raise

finally:
    db.close()
