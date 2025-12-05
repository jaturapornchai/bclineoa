import os
import httpx
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")


class LineService:
    """LINE Messaging API Service"""

    BASE_URL = "https://api.line.me/v2/bot"

    @classmethod
    def _get_headers(cls) -> dict:
        return {
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

    @classmethod
    async def reply_message(cls, reply_token: str, message: str) -> bool:
        """ตอบกลับข้อความผ่าน reply token"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{cls.BASE_URL}/message/reply",
                    headers=cls._get_headers(),
                    json={
                        "replyToken": reply_token,
                        "messages": [
                            {
                                "type": "text",
                                "text": message
                            }
                        ]
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    return True
                else:
                    print(f"LINE Reply Error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            print(f"LINE Reply Exception: {e}")
            return False

    @classmethod
    async def push_message(cls, user_id: str, message: str) -> bool:
        """ส่งข้อความไปหาผู้ใช้โดยตรง (Push Message)"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{cls.BASE_URL}/message/push",
                    headers=cls._get_headers(),
                    json={
                        "to": user_id,
                        "messages": [
                            {
                                "type": "text",
                                "text": message
                            }
                        ]
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    return True
                else:
                    print(f"LINE Push Error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            print(f"LINE Push Exception: {e}")
            return False

    @classmethod
    async def multicast_message(cls, user_ids: List[str], message: str) -> bool:
        """ส่งข้อความไปหาผู้ใช้หลายคนพร้อมกัน"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{cls.BASE_URL}/message/multicast",
                    headers=cls._get_headers(),
                    json={
                        "to": user_ids,
                        "messages": [
                            {
                                "type": "text",
                                "text": message
                            }
                        ]
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    return True
                else:
                    print(f"LINE Multicast Error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            print(f"LINE Multicast Exception: {e}")
            return False

    @classmethod
    async def broadcast_message(cls, message: str) -> bool:
        """ส่งข้อความไปหาผู้ใช้ทุกคนที่ add เป็นเพื่อน"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{cls.BASE_URL}/message/broadcast",
                    headers=cls._get_headers(),
                    json={
                        "messages": [
                            {
                                "type": "text",
                                "text": message
                            }
                        ]
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    return True
                else:
                    print(f"LINE Broadcast Error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            print(f"LINE Broadcast Exception: {e}")
            return False

    @classmethod
    async def get_user_profile(cls, user_id: str) -> Optional[dict]:
        """ดึงข้อมูล profile ของผู้ใช้"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{cls.BASE_URL}/profile/{user_id}",
                    headers=cls._get_headers(),
                    timeout=10.0
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"LINE Profile Error: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            print(f"LINE Profile Exception: {e}")
            return None
