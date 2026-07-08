"""Research report endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.models import Report
from app.serializers import report_dict

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    reps = db.execute(select(Report).order_by(Report.id.desc())).scalars().all()
    return {"reports": [report_dict(r, include_markdown=False) for r in reps]}


@router.get("/{rid}")
def get_report(rid: int, db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r:
        raise HTTPException(404, "Report not found")
    return report_dict(r)


@router.get("/{rid}/download")
def download_report(rid: int, db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r:
        raise HTTPException(404, "Report not found")
    return Response(
        content=r.markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="report_{rid}.md"'},
    )
