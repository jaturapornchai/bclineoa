from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from typing import Optional, List
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DBNAME = os.getenv("MONGODB_DBNAME", "bc_line_bot")
SHOP_ID = os.getenv("SHOP_ID", "default_shop") # เพิ่ม shopid เริ่มต้น


class Database:
    client: AsyncIOMotorClient = None
    db = None

    @classmethod
    async def connect(cls):
        cls.client = AsyncIOMotorClient(MONGODB_URI)
        cls.db = cls.client[MONGODB_DBNAME]
        print(f"Connected to MongoDB: {MONGODB_DBNAME}")

    @classmethod
    async def disconnect(cls):
        if cls.client:
            cls.client.close()
            print("Disconnected from MongoDB")

    @classmethod
    def get_db(cls):
        return cls.db


class UserRepository:
    collection_name = "users"

    @classmethod
    async def get_collection(cls):
        return Database.get_db()[cls.collection_name]

    @classmethod
    async def create_pending_user(cls, line_user_id: str, display_name: str = None, picture_url: str = None) -> dict:
        """สร้างผู้ใช้ที่รอลงทะเบียน"""
        collection = await cls.get_collection()

        user_data = {
            "line_user_id": line_user_id,
            "display_name": display_name,
            "picture_url": picture_url,
            "registered": False,
            "registration_code": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        result = await collection.find_one_and_update(
            {"line_user_id": line_user_id},
            {
                "$set": user_data,
                "$setOnInsert": {"created_at": datetime.utcnow()}
            },
            upsert=True,
            return_document=True
        )
        return result

    @classmethod
    async def register_user(cls, line_user_id: str, registration_code: str) -> dict:
        """ลงทะเบียนผู้ใช้ด้วยเลข 4 หลัก"""
        collection = await cls.get_collection()

        result = await collection.find_one_and_update(
            {"line_user_id": line_user_id},
            {
                "$set": {
                    "registered": True,
                    "registration_code": registration_code,
                    "registered_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
            },
            return_document=True
        )
        return result

    @classmethod
    async def get_user(cls, line_user_id: str) -> Optional[dict]:
        """ดึงข้อมูลผู้ใช้จาก line_user_id"""
        collection = await cls.get_collection()
        return await collection.find_one({"line_user_id": line_user_id})

    @classmethod
    async def get_all_users(cls) -> List[dict]:
        """ดึงข้อมูลผู้ใช้ทั้งหมด"""
        collection = await cls.get_collection()
        cursor = collection.find({})
        return await cursor.to_list(length=None)


class ChatHistoryRepository:
    collection_name = "chat_history"

    @classmethod
    async def get_collection(cls):
        return Database.get_db()[cls.collection_name]

    @classmethod
    async def add_message(cls, line_user_id: str, role: str, content: str) -> dict:
        """เพิ่มข้อความใหม่ใน chat history"""
        collection = await cls.get_collection()

        message = {
            "line_user_id": line_user_id,
            "role": role,  # "user" หรือ "assistant"
            "content": content,
            "created_at": datetime.utcnow()
        }

        result = await collection.insert_one(message)
        message["_id"] = result.inserted_id
        return message

    @classmethod
    async def get_history(cls, line_user_id: str, limit: int = 10) -> List[dict]:
        """ดึง chat history ของผู้ใช้ (เรียงจากเก่าไปใหม่)"""
        collection = await cls.get_collection()

        cursor = collection.find(
            {"line_user_id": line_user_id}
        ).sort("created_at", -1).limit(limit)

        messages = await cursor.to_list(length=limit)
        # เรียงจากเก่าไปใหม่สำหรับส่งให้ AI
        return list(reversed(messages))

    @classmethod
    async def clear_history(cls, line_user_id: str) -> int:
        """ลบ chat history ของผู้ใช้"""
        collection = await cls.get_collection()
        result = await collection.delete_many({"line_user_id": line_user_id})
        return result.deleted_count


class RegistrationRepository:
    collection_name = "bcagent_line_registrations"

    @classmethod
    async def get_collection(cls):
        return Database.get_db()[cls.collection_name]

    @classmethod
    async def find_and_claim_registration(cls, registration_code: str, line_user_id: str) -> Optional[dict]:
        """
        ค้นหารหัสลงทะเบียนที่ยังไม่หมดอายุและยังเป็น 'pending'
        ถ้าเจอจะอัปเดตสถานะเป็น 'completed' และใส่ line_user_id
        """
        collection = await cls.get_collection()
        now = datetime.utcnow().isoformat()

        # Atomic operation to find and update
        # Data is stored inside 'data' field
        updated_doc = await collection.find_one_and_update(
            {
                "data.registration_code": registration_code,
                "data.status": "pending",
                "data.expires_at": {"$gt": now}
            },
            {
                "$set": {
                    "data.status": "completed", 
                    "data.line_user_id": line_user_id,
                    "data.completed_at": now
                }
            },
            return_document=True # Return the document *after* update
        )
        return updated_doc
