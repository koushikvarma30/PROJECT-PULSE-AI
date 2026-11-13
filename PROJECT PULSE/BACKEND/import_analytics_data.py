from pathlib import Path
import pandas as pd
from database import Base, engine, SessionLocal
from models import AnalyticsRecord

# Put PROJECT_PULSE_CLEANED_DATA.xlsx in the same BACKEND folder as this script.
WORKBOOK = Path(__file__).with_name("PROJECT_PULSE_CLEANED_DATA.xlsx")
SHEET = "Cleaned_Data"

def clean_text(v):
    if pd.isna(v): return None
    return str(v).strip()

def num(v, default=0):
    if pd.isna(v) or str(v).strip()=="": return default
    try: return float(v)
    except (TypeError, ValueError): return default

def main():
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"Workbook not found: {WORKBOOK}\nCopy PROJECT_PULSE_CLEANED_DATA.xlsx into the BACKEND folder.")
    frame = pd.read_excel(WORKBOOK, sheet_name=SHEET)
    expected = ["S.No", "Employee ID", "Name", "Project", "Role", "Skills", "Experience (Years)", "No. of Projects Handled", "Rating (out of 5)", "Task Completion (%)", "Availability Status", "Location", "Education", "Monthly Cost (INR)", "Date Joined", "Team Size", "Bugs Resolved", "On-time Delivery (%)"]
    missing = [c for c in expected if c not in frame.columns]
    if missing: raise ValueError(f"Workbook is missing required columns: {missing}")
    Base.metadata.create_all(bind=engine)
    rows=[]
    for _, r in frame.iterrows():
        rows.append(AnalyticsRecord(
            source_s_no=int(num(r["S.No"],0)), employee_id=clean_text(r["Employee ID"]),
            name=clean_text(r["Name"]) or clean_text(r["Employee ID"]) or "Unknown",
            project=clean_text(r["Project"]), role=clean_text(r["Role"]), skills=clean_text(r["Skills"]) or "",
            experience_years=num(r["Experience (Years)"]), projects_handled=int(num(r["No. of Projects Handled"])),
            rating=num(r["Rating (out of 5"] if "Rating (out of 5" in frame.columns else r["Rating (out of 5)"]),
            task_completion=num(r["Task Completion (%)"]), availability_status=clean_text(r["Availability Status"]),
            location=clean_text(r["Location"]), education=clean_text(r["Education"]),
            monthly_cost_inr=num(r["Monthly Cost (INR)"]),
            date_joined=(r["Date Joined"].strftime("%Y-%m-%d") if pd.notna(r["Date Joined"]) and hasattr(r["Date Joined"],"strftime") else clean_text(r["Date Joined"])),
            team_size=int(num(r["Team Size"])), bugs_resolved=int(num(r["Bugs Resolved"])),
            on_time_delivery=num(r["On-time Delivery (%)"])
        ))
    db=SessionLocal()
    try:
        # Replace this analytics table only; project/task/bug tables remain untouched.
        db.query(AnalyticsRecord).delete(synchronize_session=False)
        db.bulk_save_objects(rows)
        db.commit()
        print(f"Imported {len(rows):,} cleaned analytics records into analytics_records.")
        print("Project Pulse operational tables were not changed.")
    except Exception:
        db.rollback(); raise
    finally: db.close()

if __name__ == "__main__": main()
