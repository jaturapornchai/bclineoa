import os
import hashlib
import hmac
import base64
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient, ReturnDocument

# ==================== Config ====================
LINE_CHANNEL_ACCESS_TOKEN = (os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
LINE_CHANNEL_SECRET = (os.getenv("LINE_CHANNEL_SECRET") or "").strip()
MONGODB_URI = (os.getenv("MONGODB_URI") or "").strip()
MONGODB_DBNAME = (os.getenv("MONGODB_DBNAME") or "bc_line_bot").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()


# ==================== Database ====================
def get_db():
    client = MongoClient(MONGODB_URI)
    return client[MONGODB_DBNAME]


class UserRepository:
    @classmethod
    def create_pending_user(cls, line_user_id: str, display_name: str = None, picture_url: str = None) -> dict:
        """สร้างผู้ใช้ที่รอลงทะเบียน"""
        db = get_db()
        collection = db["users"]
        user_data = {
            "line_user_id": line_user_id,
            "display_name": display_name,
            "picture_url": picture_url,
            "registered": False,
            "registration_code": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        result = collection.find_one_and_update(
            {"line_user_id": line_user_id},
            {"$set": user_data, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return result

    @classmethod
    def register_user(cls, line_user_id: str, registration_code: str) -> dict:
        """ลงทะเบียนผู้ใช้ด้วยเลข 4 หลัก"""
        db = get_db()
        collection = db["users"]
        result = collection.find_one_and_update(
            {"line_user_id": line_user_id},
            {
                "$set": {
                    "registered": True,
                    "registration_code": registration_code,
                    "registered_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
            },
            return_document=ReturnDocument.AFTER
        )
        return result

    @classmethod
    def get_user(cls, line_user_id: str) -> Optional[dict]:
        db = get_db()
        return db["users"].find_one({"line_user_id": line_user_id})

    @classmethod
    def get_all_users(cls) -> List[dict]:
        db = get_db()
        return list(db["users"].find({}))


class ChatHistoryRepository:
    @classmethod
    def add_message(cls, line_user_id: str, role: str, content: str) -> dict:
        db = get_db()
        message = {
            "line_user_id": line_user_id,
            "role": role,
            "content": content,
            "created_at": datetime.utcnow()
        }
        result = db["chat_history"].insert_one(message)
        message["_id"] = result.inserted_id
        return message

    @classmethod
    def get_history(cls, line_user_id: str, limit: int = 10) -> List[dict]:
        db = get_db()
        cursor = db["chat_history"].find({"line_user_id": line_user_id}).sort("created_at", -1).limit(limit)
        messages = list(cursor)
        return list(reversed(messages))

    @classmethod
    def clear_history(cls, line_user_id: str) -> int:
        db = get_db()
        result = db["chat_history"].delete_many({"line_user_id": line_user_id})
        return result.deleted_count


# ==================== AI Service ====================
class AIService:
    SYSTEM_PROMPT = """คุณเป็นผู้ช่วย AI ที่เป็นมิตรและช่วยเหลือผู้ใช้ได้ดี
ตอบคำถามเป็นภาษาไทยอย่างสุภาพและเป็นกันเอง
ถ้าไม่แน่ใจในคำตอบ ให้บอกตรงๆ ว่าไม่แน่ใจ"""

    @classmethod
    def get_response(cls, user_message: str, chat_history: List[dict] = None) -> str:
        if not GEMINI_API_KEY:
            return "ขออภัย ยังไม่ได้ตั้งค่า GEMINI_API_KEY"

        try:
            contents = [
                {"role": "user", "parts": [{"text": cls.SYSTEM_PROMPT}]},
                {"role": "model", "parts": [{"text": "เข้าใจแล้วครับ ผมพร้อมช่วยเหลือคุณแล้ว"}]}
            ]

            if chat_history:
                for msg in chat_history:
                    role = "user" if msg["role"] == "user" else "model"
                    contents.append({"role": role, "parts": [{"text": msg["content"]}]})

            contents.append({"role": "user", "parts": [{"text": user_message}]})

            with httpx.Client() as client:
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
                    headers={"content-type": "application/json"},
                    json={"contents": contents, "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.7}},
                    timeout=30.0
                )

                if response.status_code == 200:
                    data = response.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    print(f"Gemini API Error: {response.status_code} - {response.text}")
                    return "ขออภัย เกิดข้อผิดพลาดในการเชื่อมต่อ AI"

        except Exception as e:
            print(f"Gemini API Exception: {e}")
            return "ขออภัย เกิดข้อผิดพลาดในการเชื่อมต่อ AI"


# ==================== LINE Service ====================
class LineService:
    BASE_URL = "https://api.line.me/v2/bot"

    @classmethod
    def _get_headers(cls) -> dict:
        return {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

    @classmethod
    def reply_message(cls, reply_token: str, message: str) -> bool:
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{cls.BASE_URL}/message/reply",
                    headers=cls._get_headers(),
                    json={"replyToken": reply_token, "messages": [{"type": "text", "text": message}]},
                    timeout=10.0
                )
                return response.status_code == 200
        except Exception as e:
            print(f"LINE Reply Exception: {e}")
            return False

    @classmethod
    def reply_messages(cls, reply_token: str, messages: List[str]) -> bool:
        """ส่งหลายข้อความพร้อมกัน"""
        try:
            msg_list = [{"type": "text", "text": msg} for msg in messages]
            with httpx.Client() as client:
                response = client.post(
                    f"{cls.BASE_URL}/message/reply",
                    headers=cls._get_headers(),
                    json={"replyToken": reply_token, "messages": msg_list},
                    timeout=10.0
                )
                return response.status_code == 200
        except Exception as e:
            print(f"LINE Reply Exception: {e}")
            return False

    @classmethod
    def push_message(cls, user_id: str, message: str) -> bool:
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{cls.BASE_URL}/message/push",
                    headers=cls._get_headers(),
                    json={"to": user_id, "messages": [{"type": "text", "text": message}]},
                    timeout=10.0
                )
                return response.status_code == 200
        except Exception as e:
            print(f"LINE Push Exception: {e}")
            return False

    @classmethod
    def multicast_message(cls, user_ids: List[str], message: str) -> bool:
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{cls.BASE_URL}/message/multicast",
                    headers=cls._get_headers(),
                    json={"to": user_ids, "messages": [{"type": "text", "text": message}]},
                    timeout=10.0
                )
                return response.status_code == 200
        except Exception as e:
            print(f"LINE Multicast Exception: {e}")
            return False

    @classmethod
    def broadcast_message(cls, message: str) -> bool:
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{cls.BASE_URL}/message/broadcast",
                    headers=cls._get_headers(),
                    json={"messages": [{"type": "text", "text": message}]},
                    timeout=10.0
                )
                return response.status_code == 200
        except Exception as e:
            print(f"LINE Broadcast Exception: {e}")
            return False

    @classmethod
    def get_user_profile(cls, user_id: str) -> Optional[dict]:
        try:
            with httpx.Client() as client:
                response = client.get(
                    f"{cls.BASE_URL}/profile/{user_id}",
                    headers=cls._get_headers(),
                    timeout=10.0
                )
                return response.json() if response.status_code == 200 else None
        except Exception as e:
            print(f"LINE Profile Exception: {e}")
            return None


# ==================== FastAPI App ====================
app = FastAPI(title="LINE OA Chatbot")


def verify_signature(body: bytes, signature: str) -> bool:
    if not LINE_CHANNEL_SECRET:
        return True
    hash_value = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected_signature = base64.b64encode(hash_value).decode()
    return hmac.compare_digest(signature, expected_signature)


def handle_message_event(event: dict):
    user_id = event["source"]["userId"]
    reply_token = event["replyToken"]
    message = event["message"]

    if message["type"] != "text":
        LineService.reply_message(reply_token, "ขออภัย ตอนนี้รองรับเฉพาะข้อความตัวอักษรเท่านั้น")
        return

    user_message = message["text"].strip()

    # ตรวจสอบสถานะผู้ใช้
    user = UserRepository.get_user(user_id)
    
    # ถ้ายังไม่มีผู้ใช้ในระบบ ให้สร้างและขอลงทะเบียน
    if not user:
        profile = LineService.get_user_profile(user_id)
        UserRepository.create_pending_user(
            line_user_id=user_id,
            display_name=profile.get("displayName") if profile else None,
            picture_url=profile.get("pictureUrl") if profile else None
        )
        LineService.reply_message(reply_token, "🔐 กรุณากรอกรหัสลงทะเบียน 4 หลักเพื่อเริ่มใช้งาน")
        return
    
    # ถ้ายังไม่ได้ลงทะเบียน ให้รอรับเลข 4 หลัก
    if not user.get("registered", False):
        # ตรวจสอบว่าเป็นเลข 4 หลักหรือไม่
        if user_message.isdigit() and len(user_message) == 4:
            UserRepository.register_user(user_id, user_message)
            display_name = user.get("display_name", "คุณ")
            LineService.reply_message(reply_token, f"✅ ลงทะเบียนสำเร็จ!\n\nยินดีต้อนรับคุณ {display_name} 🎉\nตอนนี้คุณสามารถใช้งาน AI Chatbot ได้แล้ว\n\nพิมพ์ /clear เพื่อลบประวัติแชท")
            return
        else:
            LineService.reply_message(reply_token, "❌ กรุณากรอกรหัสเป็นตัวเลข 4 หลัก")
            return

    # ผู้ใช้ลงทะเบียนแล้ว ดำเนินการปกติ
    # ตรวจสอบคำสั่งพิเศษ
    if user_message.lower() == "/clear":
        deleted = ChatHistoryRepository.clear_history(user_id)
        LineService.reply_message(reply_token, f"ลบประวัติแชท {deleted} ข้อความแล้ว")
        return

    history = ChatHistoryRepository.get_history(user_id, limit=10)
    ai_response = AIService.get_response(user_message, history)

    ChatHistoryRepository.add_message(user_id, "user", user_message)
    ChatHistoryRepository.add_message(user_id, "assistant", ai_response)
    LineService.reply_message(reply_token, ai_response)


def handle_follow_event(event: dict):
    user_id = event["source"]["userId"]
    reply_token = event["replyToken"]

    profile = LineService.get_user_profile(user_id)
    display_name = profile.get("displayName", "คุณ") if profile else "คุณ"

    UserRepository.create_pending_user(
        line_user_id=user_id,
        display_name=display_name,
        picture_url=profile.get("pictureUrl") if profile else None
    )

    welcome_message = f"สวัสดีครับ {display_name}! 🙏\n\nยินดีต้อนรับสู่ AI Chatbot\n\n🔐 กรุณากรอกรหัสลงทะเบียน 4 หลักเพื่อเริ่มใช้งาน"
    LineService.reply_message(reply_token, welcome_message)


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    # ถ้าไม่มี body ให้ return OK เลย
    if not body:
        return {"status": "ok"}

    try:
        data = await request.json()
    except Exception:
        return {"status": "ok"}

    events = data.get("events", [])

    # ถ้าไม่มี events (verification request จาก LINE) ให้ return OK
    if not events:
        return {"status": "ok"}

    # Verify signature เฉพาะเมื่อมี events จริงๆ
    if signature and LINE_CHANNEL_SECRET:
        if not verify_signature(body, signature):
            print(f"Invalid signature received")
            # ยังคง process ต่อไป แต่ log warning

    for event in events:
        try:
            event_type = event.get("type")
            if event_type == "message":
                handle_message_event(event)
            elif event_type == "follow":
                handle_follow_event(event)
        except Exception as e:
            print(f"Error handling event: {e}")

    return {"status": "ok"}


# ==================== API Endpoints ====================
class PushMessageRequest(BaseModel):
    user_id: str
    message: str


class MulticastRequest(BaseModel):
    user_ids: List[str]
    message: str


class BroadcastRequest(BaseModel):
    message: str


@app.post("/api/push")
def push_message_api(req: PushMessageRequest):
    success = LineService.push_message(req.user_id, req.message)
    if success:
        return {"status": "success", "message": "Message sent"}
    raise HTTPException(status_code=500, detail="Failed to send message")


@app.post("/api/multicast")
def multicast_message_api(req: MulticastRequest):
    success = LineService.multicast_message(req.user_ids, req.message)
    if success:
        return {"status": "success", "message": "Message sent to multiple users"}
    raise HTTPException(status_code=500, detail="Failed to send messages")


@app.post("/api/broadcast")
def broadcast_message_api(req: BroadcastRequest):
    success = LineService.broadcast_message(req.message)
    if success:
        return {"status": "success", "message": "Message broadcasted"}
    raise HTTPException(status_code=500, detail="Failed to broadcast message")


@app.get("/api/users")
def get_all_users_api():
    users = UserRepository.get_all_users()
    for user in users:
        user["_id"] = str(user["_id"])
    return {"users": users}


@app.get("/api/users/{user_id}")
def get_user_api(user_id: str):
    user = UserRepository.get_user(user_id)
    if user:
        user["_id"] = str(user["_id"])
        return user
    raise HTTPException(status_code=404, detail="User not found")


@app.get("/api/users/{user_id}/history")
def get_chat_history_api(user_id: str, limit: int = 20):
    history = ChatHistoryRepository.get_history(user_id, limit)
    for msg in history:
        msg["_id"] = str(msg["_id"])
    return {"history": history}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/")
def root():
    return {"message": "LINE OA Chatbot API", "status": "running"}
