from sqlalchemy import Column,Integer,String,JSON,Text,Float
from src.config.database import Base
class ReviewJob(Base):
 __tablename__='yield_review_jobs'
 id=Column(Integer,primary_key=True); name=Column(String(120)); status=Column(String(20),index=True)
 department=Column(String(100)); policy_id=Column(Integer); date_from=Column(String(40)); date_to=Column(String(40))
 penalty=Column(Float); request_key=Column(String(64),unique=True,index=True); checksum=Column(String(64))
 snapshot=Column(JSON); result=Column(JSON); checkpoint=Column(JSON); error=Column(Text)
 created=Column(Float); updated=Column(Float); created_by=Column(String(120))
