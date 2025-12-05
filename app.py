import os
import hashlib
import hmac
import base64
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv

from database import Database, UserRepository, ChatHistoryRepository
from services.ai_service import AIService
from services.line_service import LineService

load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await Database.connect()
    yield
    # Shutdown
    await Database.disconnect()


app = FastAPI(
    title="LINE OA Chatbot",
    description="LINE OA Chatbot with AI (Gemini) and MongoDB",
    lifespan=lifespan
)


def verify_signature(body: bytes, signature: str) -> bool:
    """ตรวจสอบ signature จาก LINE"""
    if not LINE_CHANNEL_SECRET:
        return True  # Skip verification if no secret

    hash_value = hmac.new(
        LINE_CHANNEL_SECRET.encode(),
        body,
        hashlib.sha256
    ).digest()
    expected_signature = base64.b64encode(hash_value).decode()
    return hmac.compare_digest(signature, expected_signature)


async def handle_message_event(event: dict):
    """จัดการ message event จาก LINE"""
    user_id = event["source"]["userId"]
    reply_token = event["replyToken"]
    message = event["message"]

    if message["type"] != "text":
        await LineService.reply_message(reply_token, "ขออภัย ตอนนี้รองรับเฉพาะข้อความตัวอักษรเท่านั้น")
        return

    user_message = message["text"]

    # ตรวจสอบคำสั่งพิเศษ
    if user_message.lower() == "/clear":
        deleted = await ChatHistoryRepository.clear_history(user_id)
        await LineService.reply_message(reply_token, f"ลบประวัติแชท {deleted} ข้อความแล้ว")
        return

    # ดึง profile และลงทะเบียนผู้ใช้
    profile = await LineService.get_user_profile(user_id)
    if profile:
        await UserRepository.register_user(
            line_user_id=user_id,
            display_name=profile.get("displayName"),
            picture_url=profile.get("pictureUrl")
        )
    else:
        await UserRepository.register_user(line_user_id=user_id)

    # ดึง chat history
    history = await ChatHistoryRepository.get_history(user_id, limit=10)

    # ส่งให้ AI ตอบ
    ai_response = await AIService.get_response(user_message, history)

    # บันทึก chat history
    await ChatHistoryRepository.add_message(user_id, "user", user_message)
    await ChatHistoryRepository.add_message(user_id, "assistant", ai_response)

    # ส่งข้อความตอบกลับ
    await LineService.reply_message(reply_token, ai_response)


async def handle_follow_event(event: dict):
    """จัดการเมื่อมีคน add เป็นเพื่อน"""
    user_id = event["source"]["userId"]
    reply_token = event["replyToken"]

    profile = await LineService.get_user_profile(user_id)
    display_name = profile.get("displayName", "คุณ") if profile else "คุณ"

    await UserRepository.register_user(
        line_user_id=user_id,
        display_name=display_name,
        picture_url=profile.get("pictureUrl") if profile else None
    )

    welcome_message = f"สวัสดีครับ {display_name}! 🙏\n\nยินดีต้อนรับสู่ AI Chatbot\nพิมพ์ข้อความมาได้เลยครับ ผมพร้อมช่วยเหลือคุณ\n\nพิมพ์ /clear เพื่อลบประวัติแชท"
    await LineService.reply_message(reply_token, welcome_message)


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """LINE Webhook endpoint"""
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = await request.json()
    events = data.get("events", [])

    for event in events:
        event_type = event.get("type")

        if event_type == "message":
            background_tasks.add_task(handle_message_event, event)
        elif event_type == "follow":
            background_tasks.add_task(handle_follow_event, event)

    return {"status": "ok"}


# ==================== API Endpoints สำหรับส่งข้อความ ====================

class PushMessageRequest(BaseModel):
    user_id: str
    message: str


class MulticastRequest(BaseModel):
    user_ids: List[str]
    message: str


class BroadcastRequest(BaseModel):
    message: str


@app.post("/api/push")
async def push_message(req: PushMessageRequest):
    """ส่งข้อความไปหาผู้ใช้คนเดียว"""
    success = await LineService.push_message(req.user_id, req.message)
    if success:
        return {"status": "success", "message": "Message sent"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send message")


@app.post("/api/multicast")
async def multicast_message(req: MulticastRequest):
    """ส่งข้อความไปหาผู้ใช้หลายคน"""
    success = await LineService.multicast_message(req.user_ids, req.message)
    if success:
        return {"status": "success", "message": "Message sent to multiple users"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send messages")


@app.post("/api/broadcast")
async def broadcast_message(req: BroadcastRequest):
    """ส่งข้อความไปหาผู้ใช้ทุกคน"""
    success = await LineService.broadcast_message(req.message)
    if success:
        return {"status": "success", "message": "Message broadcasted"}
    else:
        raise HTTPException(status_code=500, detail="Failed to broadcast message")


@app.get("/api/users")
async def get_all_users():
    """ดึงรายชื่อผู้ใช้ทั้งหมด"""
    users = await UserRepository.get_all_users()
    # แปลง ObjectId เป็น string
    for user in users:
        user["_id"] = str(user["_id"])
    return {"users": users}


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    """ดึงข้อมูลผู้ใช้"""
    user = await UserRepository.get_user(user_id)
    if user:
        user["_id"] = str(user["_id"])
        return user
    else:
        raise HTTPException(status_code=404, detail="User not found")


@app.get("/api/users/{user_id}/history")
async def get_chat_history(user_id: str, limit: int = 20):
    """ดึงประวัติแชทของผู้ใช้"""
    history = await ChatHistoryRepository.get_history(user_id, limit)
    for msg in history:
        msg["_id"] = str(msg["_id"])
    return {"history": history}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=True)
