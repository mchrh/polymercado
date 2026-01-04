from __future__ import annotations

import time
from typing import Callable

from sqlalchemy.orm import Session

from polymercado.models import JobRun
from polymercado.utils import utc_now


JobFunc = Callable[[Session], int]


def run_job(session: Session, job_name: str, func: JobFunc) -> int:
    started_at = utc_now()
    job_run = session.get(JobRun, job_name)
    if job_run is None:
        job_run = JobRun(job_name=job_name)
        session.add(job_run)
    job_run.last_started_at = started_at
    session.commit()

    start_time = time.monotonic()
    try:
        processed = func(session)
        job_run.last_success_at = utc_now()
        job_run.last_error = None
        job_run.last_error_at = None
        return processed
    except Exception as exc:
        job_run.last_error_at = utc_now()
        job_run.last_error = str(exc)
        raise
    finally:
        duration_ms = (time.monotonic() - start_time) * 1000
        job_run.last_duration_ms = duration_ms
        session.commit()
