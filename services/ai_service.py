import os
import httpx
from typing import List
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


class AIService:
    """AI Service สำหรับตอบกลับข้อความ (ใช้ Gemini)"""

    SYSTEM_PROMPT = """คุณเป็นผู้ช่วย AI ที่เป็นมิตรและช่วยเหลือผู้ใช้ได้ดี
ตอบคำถามเป็นภาษาไทยอย่างสุภาพและเป็นกันเอง
ถ้าไม่แน่ใจในคำตอบ ให้บอกตรงๆ ว่าไม่แน่ใจ"""

    @classmethod
    async def get_response(cls, user_message: str, chat_history: List[dict] = None) -> str:
        """รับข้อความจากผู้ใช้และส่งกลับคำตอบจาก Gemini"""
        if not GEMINI_API_KEY:
            return "ขออภัย ยังไม่ได้ตั้งค่า GEMINI_API_KEY"

        try:
            # สร้าง contents สำหรับ Gemini
            contents = []

            # เพิ่ม system prompt เป็นข้อความแรก
            contents.append({
                "role": "user",
                "parts": [{"text": cls.SYSTEM_PROMPT}]
            })
            contents.append({
                "role": "model",
                "parts": [{"text": "เข้าใจแล้วครับ ผมพร้อมช่วยเหลือคุณแล้ว"}]
            })

            # เพิ่ม chat history
            if chat_history:
                for msg in chat_history:
                    role = "user" if msg["role"] == "user" else "model"
                    contents.append({
                        "role": role,
                        "parts": [{"text": msg["content"]}]
                    })

            # เพิ่มข้อความใหม่
            contents.append({
                "role": "user",
                "parts": [{"text": user_message}]
            })

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
                    headers={"content-type": "application/json"},
                    json={
                        "contents": contents,
                        "generationConfig": {
                            "maxOutputTokens": 1024,
                            "temperature": 0.7
                        }
                    },
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
