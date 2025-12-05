from pydantic import BaseModel
from typing import List


class PushMessageRequest(BaseModel):
    user_id: str
    message: str


class MulticastRequest(BaseModel):
    user_ids: List[str]
    message: str


class BroadcastRequest(BaseModel):
    message: str
