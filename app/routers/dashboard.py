import datetime
from fastapi import APIRouter, Depends
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict, str_to_objectid
from ..schemas import DashboardStats, SearchHistoryOut
from ..auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

ALL_RESOLVED = {"resolved", "solved", "found", "traced_resolved", "closed"}


def _build_search_history_out(search_doc: dict, db: Database) -> dict:
    result = doc_to_dict(search_doc)
    matched_person_id = result.get("matched_person_id")
    if matched_person_id:
        try:
            person_doc = db["missing_persons"].find_one({"_id": str_to_objectid(matched_person_id)})
            result["matched_person"] = doc_to_dict(person_doc) if person_doc else None
        except Exception:
            result["matched_person"] = None
    else:
        result["matched_person"] = None
    return result


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats(
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    # ── Live counts — case-insensitive for robustness ────────────────────────
    all_persons = list(db["missing_persons"].find({}, {"status": 1}))
    total_missing  = len(all_persons)
    active_cases   = sum(1 for p in all_persons if (p.get("status","Active") or "Active").lower() == "active")
    tracing_cases  = sum(1 for p in all_persons if (p.get("status","") or "").lower() == "tracing")
    resolved_cases = sum(1 for p in all_persons if (p.get("status","") or "").lower() in {"resolved","solved","found","traced","closed_resolved"})
    closed_cases   = sum(1 for p in all_persons if (p.get("status","") or "").lower() == "closed")
    searches_count = db["search_histories"].count_documents({})

    # ── Recent searches (top 10) ──────────────────────────────────────────────
    recent_search_docs = list(
        db["search_histories"].find({}).sort("search_date", -1).limit(10)
    )
    recent_searches = [_build_search_history_out(doc, db) for doc in recent_search_docs]

    # ── Analytics: last 6 months — all 4 statuses ────────────────────────────
    cases_over_time = []
    today = datetime.date.today()
    for i in range(5, -1, -1):
        year  = today.year
        month = today.month - i
        if month <= 0:
            month += 12
            year  -= 1

        month_start = datetime.datetime(year, month, 1)
        next_month_start = (
            datetime.datetime(year + 1, 1, 1)
            if month == 12
            else datetime.datetime(year, month + 1, 1)
        )
        label = month_start.strftime("%b %Y")

        def _count(status_val):
            # Case-insensitive status match
            regex_pattern = f"^{status_val}$"
            return db["missing_persons"].count_documents({
                "created_at": {"$gte": month_start, "$lt": next_month_start},
                "status": {"$regex": regex_pattern, "$options": "i"}
            })

        cases_over_time.append({
            "name":     label,
            "Active":   _count("Active"),
            "Tracing":  _count("Tracing"),
            "Resolved": _count("Resolved"),
            "Closed":   _count("Closed"),
        })

    # ── Recent activity feed (from audit_logs) ────────────────────────────────
    recent_activity = []
    try:
        audit_docs = list(
            db["audit_logs"].find({}).sort("changed_at", -1).limit(10)
        )
        for doc in audit_docs:
            d = doc_to_dict(doc)
            if isinstance(d.get("changed_at"), datetime.datetime):
                d["changed_at"] = d["changed_at"].isoformat()
            recent_activity.append(d)
    except Exception:
        pass

    return {
        "total_missing_persons":      total_missing,
        "total_investigation_cases":  active_cases,
        "tracing_cases":              tracing_cases,
        "resolved_cases":             resolved_cases,
        "closed_cases":               closed_cases,
        "recent_searches_count":      searches_count,
        "recent_searches":            recent_searches,
        "cases_over_time":            cases_over_time,
        "recent_activity":            recent_activity,
    }
