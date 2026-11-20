import time,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.models import identity,source,run,review
from src.models.run import Run
from src.models.review import ReviewJob
from src.services.calculation import calculate
from src.services.review import run_review
from src.services.delivery import tick
Base.metadata.create_all(engine)
r=redis.Redis.from_url(REDIS_URL)
while True:
 Path('/tmp/worker-heartbeat').write_text(str(time.time()))
 try:
  item=r.lpop('report-jobs')
  if item:
   with Session() as db:
    job=db.get(Run,int(item))
    if job and job.status=='queued':
     job.status='running';db.commit()
     try: job.result=calculate(job.snapshot['sources'],job.snapshot['policy']);job.status='completed'
     except Exception as exc: job.status='failed';job.error=str(exc);traceback.print_exc()
     db.commit()
  review_item=r.lpop('review-jobs')
  if review_item:
   with Session() as db:
    # 中断恢复：running/queued 均会续算，检查点保证不重复扫描切点
    try: run_review(db,int(review_item))
    except Exception: traceback.print_exc()
  with Session() as db: tick(db)
 except Exception: traceback.print_exc()
 time.sleep(1)
