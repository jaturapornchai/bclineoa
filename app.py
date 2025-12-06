import os
import hashlib
import hmac
import base64
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from typing import List, Optional
from dotenv import load_dotenv

from models import PushMessageRequest, MulticastRequest, BroadcastRequest
from database import Database, UserRepository, ChatHistoryRepository, RegistrationRepository
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

    user_message = message["text"].strip()

    # ตรวจสอบสถานะผู้ใช้
    user = await UserRepository.get_user(user_id)
    
    # ถ้ายังไม่มีผู้ใช้ในระบบ ให้สร้างไว้ก่อน (แต่ไม่ต้อง return)
    if not user:
        profile = await LineService.get_user_profile(user_id)
        user = await UserRepository.create_pending_user(
            line_user_id=user_id,
            display_name=profile.get("displayName") if profile else None,
            picture_url=profile.get("pictureUrl") if profile else None
        )
    
    # ตรวจสอบว่าเป็นรหัสลงทะเบียน 4 หลักหรือไม่ (ตรวจสอบทุกครั้งที่มีเลข 4 หลัก)
    if user_message.isdigit() and len(user_message) == 4:
        # ดึง profile จาก LINE (หรือใช้ที่มีอยู่แล้ว)
        profile = user if user else await LineService.get_user_profile(user_id)
        display_name = profile.get("display_name") or profile.get("displayName") if profile else None
        picture_url = profile.get("picture_url") or profile.get("pictureUrl") if profile else None
        
        claimed_reg = await RegistrationRepository.find_and_claim_registration(
            registration_code=user_message, 
            line_user_id=user_id,
            display_name=display_name,
            picture_url=picture_url
        )
        
        if claimed_reg:
            # ลงทะเบียนสำเร็จ - ดึงชื่อร้านจาก shop object
            shop = claimed_reg.get("shop", {})
            shop_name = shop.get("shop_name", "ร้านค้า")
            await LineService.reply_message(
                reply_token, 
                f"✅ ลงทะเบียนกับ {shop_name} สำเร็จ!\n\nยินดีต้อนรับคุณ {display_name or 'ลูกค้า'} 🎉"
            )
            return
        # ถ้าไม่เจอ หรือหมดอายุ ให้ถือว่าเป็นข้อความปกติ ส่งให้ AI ตอบ

    # --- ดำเนินการปกติ (AI Chat) ---
    
    # ตรวจสอบคำสั่งพิเศษ
    if user_message.lower() == "/clear":
        deleted = await ChatHistoryRepository.clear_history(user_id)
        await LineService.reply_message(reply_token, f"ลบประวัติแชท {deleted} ข้อความแล้ว")
        return

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

    await UserRepository.create_pending_user(
        line_user_id=user_id,
        display_name=display_name,
        picture_url=profile.get("pictureUrl") if profile else None
    )

    welcome_message = f"สวัสดีครับ {display_name}! 🙏\n\nยินดีต้อนรับสู่ AI Chatbot\n\n🔐 กรุณากรอกรหัสลงทะเบียน 4 หลักเพื่อเริ่มใช้งาน"
    await LineService.reply_message(reply_token, welcome_message)


@app.post("/webhook")
async def webhook(request: Request):
    """LINE Webhook endpoint"""
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    # Log request for debugging
    print(f"Received webhook. Body length: {len(body)}, Signature: {signature[:20] if signature else 'None'}...")
    
    # Parse JSON first
    try:
        data = await request.json()
    except Exception as e:
        print(f"JSON parse error: {e}")
        return {"status": "ok"}
    
    events = data.get("events", [])
    print(f"Events count: {len(events)}")
    
    # Skip signature verification for empty events (LINE verification request)
    if not events:
        print("Empty events - verification request")
        return {"status": "ok"}
    
    # Verify signature only when there are actual events
    if signature and not verify_signature(body, signature):
        print("Invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        try:
            event_type = event.get("type")
            print(f"Processing event type: {event_type}")

            if event_type == "message":
                # Await directly instead of background_tasks for Vercel
                await handle_message_event(event)
            elif event_type == "follow":
                await handle_follow_event(event)
        except Exception as e:
            print(f"Error processing event: {e}")
            import traceback
            traceback.print_exc()

    return {"status": "ok"}


# ==================== API Endpoints สำหรับส่งข้อความ ====================
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
