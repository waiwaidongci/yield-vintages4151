import time
from fastapi import APIRouter,Depends,HTTPException,Body
from src.config.database import database
from src.controllers.auth import current,editor
from src.models.review import ReviewJob
from src.schemas.requests import ReviewInput,ReviewNote
from src.services.review import create_review
from src.services.audit import log
from src.config.settings import REDIS_URL
from src.utils.serialize import row
import redis
router=APIRouter()
@router.get('/reviews')
def reviews(db=Depends(database),u=Depends(current)):
 return [row(x) for x in db.query(ReviewJob).order_by(ReviewJob.id.desc()).all()]
@router.post('/reviews')
def create(b:ReviewInput,db=Depends(database),u=Depends(editor)):
 return row(create_review(db,u,b))
@router.get('/reviews/{id}')
def detail(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(ReviewJob,id)
 if not obj: raise HTTPException(404,'回查任务不存在')
 return row(obj)
@router.post('/reviews/{id}/resume')
def resume(id:int,b:ReviewNote|None=Body(default=None),db=Depends(database),u=Depends(editor)):
 # 求解中断/超时后恢复这次回查：沿用同一快照、口径、罚分与检查点，不新建任务
 obj=db.get(ReviewJob,id)
 if not obj: raise HTTPException(404,'回查任务不存在')
 if obj.status not in ('failed','rejected'):
  raise HTTPException(409,'仅中断或被拒绝的回查可以恢复续算')
 note=getattr(b,'note','') if b else ''
 log(db,u.email,'review.resume',str(id)+((' '+note) if note else ''))
 obj.status='queued';obj.updated=time.time();db.commit()
 redis.Redis.from_url(REDIS_URL).rpush('review-jobs',obj.id)
 return row(obj)
