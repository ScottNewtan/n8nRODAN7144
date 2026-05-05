    # telegram_bot.py — Мультиаккаунт + экспорт участников группы + мгновенная работа с любыми ID
import os
import asyncio
import requests
import re
import io
import mimetypes
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import PeerUser, PeerChannel, PeerChat, User, Channel, Chat, InputPhoneContact
from telethon.tl.functions.messages import GetDialogsRequest, GetDialogFiltersRequest, GetFullChatRequest
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import SessionPasswordNeededError, FloodWaitError, PhoneNumberInvalidError, UserPrivacyRestrictedError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from contextlib import asynccontextmanager
from typing import List, Optional, Union, Dict
import uvicorn
from datetime import datetime
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import User, Channel, Chat

API_ID = 20451896
API_HASH = "cfd7e7c339c9e2da0027d691da18588e"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bma7144.store/webhook-test/telethon")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Хранилище: имя → клиент
ACTIVE_CLIENTS = {}
# Изменяем формат: добавляем флаг needs_2fa
PENDING_AUTH = {}  # Формат: {phone: {"session_str": "...", "phone_code_hash": "...", "needs_2fa": False}}


# ==================== Модели ====================
class SendMessageReq(BaseModel):
    account: str
    chat_id: str | int
    text: str

class AddAccountReq(BaseModel):
    name: str
    session_string: str

class RemoveAccountReq(BaseModel):
    name: str

class AuthStartReq(BaseModel):
    phone: str

class AuthCodeReq(BaseModel):
    phone: str
    code: str
    phone_code_hash: str
    password: str | None = None  # Опционально для 2FA

class Auth2FAReq(BaseModel):
    phone: str
    password: str  # Обязательно для 2FA

class ExportMembersReq(BaseModel):
    account: str
    group: str | int

# ==================== Новые модели: webhook ====================
class WebhookSetReq(BaseModel):
    url: str
    secret: str | None = None

    @validator("url")
    def validate_url(cls, v: str):
        v = (v or "").strip()
        if not v:
            raise ValueError("url не должен быть пустым")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url должен начинаться с http:// или https://")
        return v

class WebhookTestReq(BaseModel):
    url: str
    secret: str | None = None

# ==================== Новые модели ====================
class DialogInfo(BaseModel):
    id: int
    title: str
    username: Optional[str] = None
    folder_names: List[str] = []
    is_group: bool
    is_channel: bool
    is_user: bool
    unread_count: int
    last_message_date: Optional[str] = None

class GetDialogsReq(BaseModel):
    account: str
    limit: int = 50
    include_folders: bool = True

class ChatMessage(BaseModel):
    id: int
    date: str
    from_id: Optional[int] = None
    text: str
    is_outgoing: bool
    has_media: bool = False
    media_type: Optional[str] = None
    media_url: Optional[str] = None
    media_filename: Optional[str] = None
    media_mime: Optional[str] = None
    
    @validator('from_id', pre=True)
    def parse_from_id(cls, v):
        if v is None:
            return None
        if isinstance(v, (PeerUser, PeerChannel, PeerChat)):
            return v.user_id if isinstance(v, PeerUser) else v.channel_id if isinstance(v, PeerChannel) else v.chat_id
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return None

class GetChatHistoryReq(BaseModel):
    account: str
    chat_id: Union[str, int]
    limit: int = 50
    offset_id: Optional[int] = None
    include_media: bool = False

# ==================== НОВАЯ МОДЕЛЬ: статус подключенного аккаунта ====================
class GetAccountStatusReq(BaseModel):
    account: str

# ==================== НОВАЯ МОДЕЛЬ: статус произвольного пользователя ====================
class GetUserStatusReq(BaseModel):
    account: str
    target: str | int

# ==================== НОВАЯ МОДЕЛЬ: отправка новым пользователям ====================
class SendToNewUserReq(BaseModel):
    account: str
    phone: str
    message: str
    first_name: str = "Contact"
    last_name: str = ""
    delete_after: bool = True

# ==================== НОВАЯ МОДЕЛЬ: добавление контакта ====================
class AddContactReq(BaseModel):
    account: str
    phone: str
    first_name: str = "Contact"
    last_name: str = ""


class EntityInfoReq(BaseModel):
    account: str
    target: str | int


class MultiEntityInfoReq(BaseModel):
    account: str
    targets: List[str | int]


class UserInfo(BaseModel):
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    about: Optional[str] = None
    is_bot: bool = False
    is_premium: bool = False
    is_verified: bool = False
    is_restricted: bool = False
    is_scam: bool = False
    is_deleted: bool = False


class ChatInfo(BaseModel):
    id: int
    title: str
    username: Optional[str] = None
    about: Optional[str] = None
    is_group: bool = False
    is_channel: bool = False
    participants_count: Optional[int] = None
    admins_count: Optional[int] = None
    online_count: Optional[int] = None

# ==================== НОВАЯ МОДЕЛЬ: получение информации описания группы/био ====================
class EntityInfoReq(BaseModel):
    account: str                 # имя аккаунта из /accounts
    target: str | int            # username (@channel), id или phone (для user по желанию)


class MultiEntityInfoReq(BaseModel):
    account: str
    targets: List[str | int]


class UserInfo(BaseModel):
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    about: Optional[str] = None
    is_bot: bool = False
    is_premium: bool = False
    is_verified: bool = False
    is_restricted: bool = False
    is_scam: bool = False
    is_deleted: bool = False


class ChatInfo(BaseModel):
    id: int
    title: str
    username: Optional[str] = None
    about: Optional[str] = None
    is_group: bool = False
    is_channel: bool = False
    participants_count: Optional[int] = None
    admins_count: Optional[int] = None
    online_count: Optional[int] = None

# ==================== Вспомогательные функции ====================
def extract_folder_title(folder_obj):
    if not hasattr(folder_obj, 'title'):
        return None
    
    title_obj = folder_obj.title
    if hasattr(title_obj, 'text'):
        return title_obj.text
    elif isinstance(title_obj, str):
        return title_obj
    return None


async def get_dialogs_with_folders_info(client: TelegramClient, limit: int = 50) -> List[DialogInfo]:
    """Получить диалоги с информацией о папках"""
    try:
        folder_info = {}
        try:
            dialog_filters_result = await client(GetDialogFiltersRequest())
            dialog_filters = getattr(dialog_filters_result, 'filters', [])
            
            for folder in dialog_filters:
                folder_title = extract_folder_title(folder)
                
                if hasattr(folder, 'id') and folder_title:
                    folder_info[folder.id] = {
                        'title': folder_title,
                        'include_peers': [],
                        'exclude_peers': []
                    }
                    
                    if hasattr(folder, 'include_peers'):
                        for peer in folder.include_peers:
                            peer_id = None
                            if hasattr(peer, 'user_id'):
                                peer_id = peer.user_id
                            elif hasattr(peer, 'chat_id'):
                                peer_id = peer.chat_id
                            elif hasattr(peer, 'channel_id'):
                                peer_id = peer.channel_id
                            
                            if peer_id:
                                folder_info[folder.id]['include_peers'].append(peer_id)
        except Exception as e:
            print(f"Ошибка получения папок: {e}")
        
        dialogs = await client.get_dialogs(limit=limit)
        dialog_to_folders = {}
        
        for folder_id, folder_data in folder_info.items():
            for peer_id in folder_data['include_peers']:
                if peer_id not in dialog_to_folders:
                    dialog_to_folders[peer_id] = []
                dialog_to_folders[peer_id].append(folder_data['title'])
        
        dialog_list = []
        for dialog in dialogs:
            entity = dialog.entity
            folder_names = []
            dialog_id = entity.id
            
            if dialog_id in dialog_to_folders:
                folder_names = dialog_to_folders[dialog_id]
            
            dialog_info = DialogInfo(
                id=entity.id,
                title=dialog.title or dialog.name or "Без названия",
                username=getattr(entity, 'username', None),
                folder_names=folder_names,
                is_group=getattr(entity, 'megagroup', False) or getattr(entity, 'gigagroup', False),
                is_channel=getattr(entity, 'broadcast', False),
                is_user=hasattr(entity, 'first_name'),
                unread_count=dialog.unread_count,
                last_message_date=dialog.date.isoformat() if dialog.date else None
            )
            dialog_list.append(dialog_info)
        
        return dialog_list
        
    except Exception as e:
        print(f"Ошибка получения диалогов: {e}")
        dialogs = await client.get_dialogs(limit=limit)
        return [DialogInfo(
            id=dialog.entity.id,
            title=dialog.title or dialog.name or "Без названия",
            username=getattr(dialog.entity, 'username', None),
            folder_names=[],
            is_group=getattr(dialog.entity, 'megagroup', False) or getattr(dialog.entity, 'gigagroup', False),
            is_channel=getattr(dialog.entity, 'broadcast', False),
            is_user=hasattr(dialog.entity, 'first_name'),
            unread_count=dialog.unread_count,
            last_message_date=dialog.date.isoformat() if dialog.date else None
        ) for dialog in dialogs]


# ==================== Lifespan ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Telegram Multi Gateway запущен")
    yield
    for client in ACTIVE_CLIENTS.values():
        await client.disconnect()
    print("Все аккаунты отключены")


app = FastAPI(title="Telegram Multi Account Gateway", lifespan=lifespan)


# ==================== Webhook управление ====================
@app.get("/webhook")
def get_webhook():
    return {
        "url": WEBHOOK_URL,
        "has_secret": bool(WEBHOOK_SECRET),
    }

@app.post("/webhook/set")
def set_webhook(req: WebhookSetReq):
    """
    Настроить URL, куда будут отправляться события новых входящих сообщений.
    Это НЕ Bot API webhook. Это ваш callback endpoint (куда мы POST'им payload).
    """
    global WEBHOOK_URL, WEBHOOK_SECRET
    WEBHOOK_URL = req.url.strip()
    if req.secret is not None:
        WEBHOOK_SECRET = req.secret
    return {"status": "ok", "url": WEBHOOK_URL, "has_secret": bool(WEBHOOK_SECRET)}

@app.post("/webhook/test")
async def test_webhook(req: WebhookTestReq):
    payload = {
        "type": "test",
        "date": datetime.now().isoformat(),
        "message": "webhook test event",
    }
    headers = {}
    if req.secret:
        headers["X-Webhook-Secret"] = req.secret

    try:
        resp = await asyncio.to_thread(
            requests.post,
            req.url,
            json=payload,
            headers=headers,
            timeout=12,
        )
        return {"status": "sent", "http_status": resp.status_code, "response_preview": (resp.text or "")[:500]}
    except Exception as e:
        raise HTTPException(400, detail=f"Ошибка отправки теста: {str(e)}")


# ==================== Авторизация ====================
@app.post("/auth/start")
async def auth_start(req: AuthStartReq):
    """Начать авторизацию: запросить код подтверждения"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    try:
        sent_code = await client.send_code_request(req.phone)
        session_str = client.session.save()
        
        PENDING_AUTH[req.phone] = {
            "session_str": session_str,
            "phone_code_hash": sent_code.phone_code_hash,
            "needs_2fa": False
        }
        
        await client.disconnect()
        
        return {
            "status": "code_sent",
            "phone": req.phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "needs_2fa": False
        }
    except Exception as e:
        await client.disconnect()
        raise HTTPException(400, detail=f"Ошибка: {str(e)}")


@app.post("/auth/complete")
async def auth_complete(req: AuthCodeReq):
    """
    Завершить авторизацию.
    Автоматически определяет нужен ли 2FA.
    """
    pending_data = PENDING_AUTH.get(req.phone)
    if not pending_data:
        raise HTTPException(400, "Нет активной авторизации")
    
    client = TelegramClient(StringSession(pending_data["session_str"]), API_ID, API_HASH)
    await client.connect()
    
    try:
        # 1. Пробуем войти с кодом
        try:
            await client.sign_in(
                phone=req.phone,
                code=req.code,
                phone_code_hash=pending_data["phone_code_hash"]
            )
            
        # 2. Если нужен пароль 2FA
        except SessionPasswordNeededError:
            # Обновляем статус в PENDING_AUTH
            PENDING_AUTH[req.phone]["needs_2fa"] = True
            
            # Если пароль уже предоставлен в этом же запросе
            if req.password:
                try:
                    await client.sign_in(password=req.password)
                except Exception as e:
                    await client.disconnect()
                    raise HTTPException(400, detail=f"Ошибка пароля 2FA: {str(e)}")
            else:
                await client.disconnect()
                # Возвращаем специальный статус для запроса пароля
                return {
                    "status": "2fa_required",
                    "phone": req.phone,
                    "needs_2fa": True,
                    "message": "Требуется пароль двухфакторной аутентификации",
                    "instructions": "Используйте /auth/2fa с параметром password"
                }
        
        # 3. Если другие ошибки с кодом
        except Exception as e:
            await client.disconnect()
            raise HTTPException(400, detail=f"Ошибка кода: {str(e)}")
        
        # 4. Если успешно (с кодом или кодом+паролем)
        session_str = client.session.save()
        del PENDING_AUTH[req.phone]
        await client.disconnect()
        
        return {
            "status": "success",
            "session_string": session_str,
            "message": "Авторизация успешна"
        }
        
    except Exception as e:
        await client.disconnect()
        raise HTTPException(500, detail=f"Неожиданная ошибка: {str(e)}")


@app.post("/auth/2fa")
async def auth_2fa(req: Auth2FAReq):
    """
    Отдельный эндпоинт для ввода пароля 2FA.
    Используется после получения статуса '2fa_required' от /auth/complete
    """
    pending_data = PENDING_AUTH.get(req.phone)
    if not pending_data:
        raise HTTPException(400, "Нет активной авторизации или сессия устарела")
    
    if not pending_data.get("needs_2fa", False):
        raise HTTPException(400, "Для этого номера не требуется 2FA")
    
    client = TelegramClient(StringSession(pending_data["session_str"]), API_ID, API_HASH)
    await client.connect()
    
    try:
        # Входим с паролем 2FA
        await client.sign_in(password=req.password)
        
        session_str = client.session.save()
        del PENDING_AUTH[req.phone]
        await client.disconnect()
        
        return {
            "status": "success",
            "session_string": session_str,
            "message": "2FA авторизация успешна"
        }
        
    except Exception as e:
        await client.disconnect()
        raise HTTPException(400, detail=f"Ошибка 2FA: {str(e)}")


# ==================== Работа с аккаунтами ====================
@app.post("/accounts/add")
async def add_account(req: AddAccountReq):
    if req.name in ACTIVE_CLIENTS:
        raise HTTPException(400, detail=f"Аккаунт {req.name} уже существует")

    client = TelegramClient(StringSession(req.session_string), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise HTTPException(400, detail="Сессия недействительна")

    await client.start()

    try:
        dialogs = await client.get_dialogs(limit=50)
        print(f"Прогрет кэш для {req.name}: {len(dialogs)} чатов")
    except Exception as e:
        print(f"Ошибка прогрева кэша: {e}")

    ACTIVE_CLIENTS[req.name] = client
    client.add_event_handler(
        lambda event: incoming_handler(event),
        events.NewMessage(incoming=True)
    )

    return {
        "status": "added",
        "account": req.name,
        "total_accounts": len(ACTIVE_CLIENTS)
    }


@app.delete("/accounts/{name}")
async def remove_account(name: str):
    client = ACTIVE_CLIENTS.pop(name, None)
    if client:
        await client.disconnect()
        return {"status": "removed", "account": name}
    raise HTTPException(404, detail="Аккаунт не найден")


@app.get("/accounts")
def list_accounts():
    return {"active_accounts": list(ACTIVE_CLIENTS.keys())}


@app.post("/accounts/status")
async def account_status(req: GetAccountStatusReq):
    """
    Получить статус "онлайн/оффлайн/недавно/..." для подключенного аккаунта.

    Важно: для "самого себя" Telegram часто возвращает Online пока клиент подключен,
    а точное "последний раз был(а) в сети" может быть недоступно.
    """
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        me = await client.get_me()
        status_obj = getattr(me, "status", None)
        status_type = status_obj.__class__.__name__ if status_obj is not None else None
        was_online = getattr(status_obj, "was_online", None)

        return {
            "status": "success",
            "account": req.account,
            "me": {
                "id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
                "first_name": getattr(me, "first_name", None),
                "last_name": getattr(me, "last_name", None),
            },
            "presence": {
                "type": status_type,
                "was_online": was_online.isoformat() if was_online else None,
            },
        }
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка получения статуса: {str(e)}")


@app.post("/users/status")
async def user_status(req: GetUserStatusReq):
    """
    Получить статус онлайна/последний онлайн для пользователя (наблюдаемого аккаунта)
    через подключенный аккаунт-наблюдатель (req.account).

    target: username (@user), числовой id, либо строка с номером телефона (если доступно).
    """
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    target = req.target
    if isinstance(target, str):
        t = target.strip()
        if t.startswith("@"):
            t = t[1:]
        if t.lstrip("-").isdigit():
            target = int(t)
        else:
            target = t

    try:
        entity = await client.get_entity(target)
    except Exception as e:
        raise HTTPException(400, detail=f"Не удалось найти пользователя '{req.target}': {str(e)}")

    if not isinstance(entity, User):
        raise HTTPException(400, detail=f"target должен указывать на пользователя, получено: {type(entity).__name__}")

    status_obj = getattr(entity, "status", None)
    status_type = status_obj.__class__.__name__ if status_obj is not None else None
    was_online = getattr(status_obj, "was_online", None)

    return {
        "status": "success",
        "observer_account": req.account,
        "target": req.target,
        "user": {
            "id": getattr(entity, "id", None),
            "username": getattr(entity, "username", None),
            "first_name": getattr(entity, "first_name", None),
            "last_name": getattr(entity, "last_name", None),
        },
        "presence": {
            "type": status_type,
            "was_online": was_online.isoformat() if was_online else None,
        },
    }


# ==================== НОВЫЙ ЭНДПОИНТ: Отправка сообщения новому пользователю ====================
@app.post("/send_to_new_user")
async def send_to_new_user(req: SendToNewUserReq):
    """
    Отправить сообщение пользователю, которого нет в контактах.
    Бот автоматически добавит пользователя в контакты, отправит сообщение
    и при необходимости удалит из контактов.
    """
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        # 1. Добавляем пользователя в контакты
        print(f"📇 Добавляю в контакты: {req.phone}")
        
        contact = InputPhoneContact(
            client_id=0,  # 0 для автоматического ID
            phone=req.phone,
            first_name=req.first_name,
            last_name=req.last_name
        )
        
        result = await client(ImportContactsRequest([contact]))
        
        if not result.users:
            raise HTTPException(400, detail=f"Пользователь не найден по номеру {req.phone}")
        
        user = result.users[0]
        print(f"✅ Успешно добавлен! ID: {user.id}, Имя: {user.first_name}")
        
        # 2. Отправляем сообщение
        print(f"📤 Отправляю сообщение пользователю {user.id}...")
        
        try:
            await client.send_message(user, req.message)
            print(f"✅ Сообщение отправлено!")
            
            # 3. Удаляем из контактов если требуется
            if req.delete_after:
                print(f"🗑️ Удаляю из контактов...")
                await client(DeleteContactsRequest(id=[user]))
                print(f"✅ Удалено из контактов")
            
            return {
                "status": "sent",
                "account": req.account,
                "phone": req.phone,
                "user_id": user.id,
                "user_info": {
                    "first_name": user.first_name,
                    "last_name": user.last_name or "",
                    "username": getattr(user, 'username', None)
                },
                "deleted_from_contacts": req.delete_after,
                "message_preview": req.message[:100] + "..." if len(req.message) > 100 else req.message
            }
            
        except FloodWaitError as e:
            print(f"⏳ Ограничение Telegram: ждите {e.seconds} секунд")
            # Удаляем пользователя из контактов, чтобы не оставлять следов
            if not req.delete_after:
                try:
                    await client(DeleteContactsRequest(id=[user]))
                except:
                    pass
            raise HTTPException(429, detail=f"Ограничение Telegram: ждите {e.seconds} секунд")
            
        except UserPrivacyRestrictedError:
            print(f"❌ Пользователь запретил получение сообщений")
            # Удаляем пользователя из контактов
            if not req.delete_after:
                try:
                    await client(DeleteContactsRequest(id=[user]))
                except:
                    pass
            raise HTTPException(403, detail="Пользователь запретил получение сообщений")
            
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
            # Удаляем пользователя из контактов в случае ошибки
            if not req.delete_after:
                try:
                    await client(DeleteContactsRequest(id=[user]))
                except:
                    pass
            raise HTTPException(500, detail=f"Ошибка отправки сообщения: {str(e)}")
            
    except PhoneNumberInvalidError:
        raise HTTPException(400, detail=f"Некорректный номер телефона: {req.phone}. Формат должен быть: +79991234567")
        
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка обработки: {str(e)}")


# ==================== НОВЫЙ ЭНДПОИНТ: Добавить контакт ====================
@app.post("/add_contact")
async def add_contact(req: AddContactReq):
    """
    Добавить контакт по номеру телефона.
    Возвращает информацию о добавленном пользователе.
    """
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        # 1. Добавляем пользователя в контакты
        print(f"📇 Добавляю контакт: {req.phone}")
        
        contact = InputPhoneContact(
            client_id=0,  # 0 для автоматического ID
            phone=req.phone,
            first_name=req.first_name,
            last_name=req.last_name
        )
        
        result = await client(ImportContactsRequest([contact]))
        
        if not result.users:
            raise HTTPException(400, detail=f"Пользователь не найден по номеру {req.phone}. "
                                         "Проверьте корректность номера и что пользователь существует в Telegram.")
        
        user = result.users[0]
        print(f"✅ Контакт успешно добавлен! ID: {user.id}, Имя: {user.first_name}")
        
        # 2. Получаем полную информацию о пользователе
        user_info = {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name or "",
            "username": getattr(user, 'username', None),
            "phone": req.phone,
            "bot": getattr(user, 'bot', False),
            "premium": getattr(user, 'premium', False),
            "verified": getattr(user, 'verified', False),
            "restricted": getattr(user, 'restricted', False),
            "scam": getattr(user, 'scam', False),
            "access_hash": user.access_hash if hasattr(user, 'access_hash') else None
        }
        
        # 3. Проверяем, есть ли у пользователя ограничения на отправку сообщений
        can_message = True
        try:
            # Пробуем отправить тестовое сообщение (не отправляем на самом деле)
            if hasattr(user, 'bot') and user.bot:
                can_message = True
            else:
                # Проверяем возможность отправки сообщений через get_entity
                await client.get_entity(user.id)
        except UserPrivacyRestrictedError:
            can_message = False
        except Exception:
            can_message = True
        
        return {
            "status": "contact_added",
            "account": req.account,
            "phone": req.phone,
            "contact": user_info,
            "metadata": {
                "can_message": can_message,
                "in_contacts": True,
                "date_added": datetime.now().isoformat(),
                "imported_count": result.imported[0] if hasattr(result, 'imported') and result.imported else 1
            },
            "message": f"Контакт '{req.first_name} {req.last_name}' успешно добавлен"
        }
        
    except PhoneNumberInvalidError:
        raise HTTPException(400, detail=f"Некорректный номер телефона: {req.phone}. "
                                     "Формат должен быть: +79991234567 (с кодом страны)")
        
    except FloodWaitError as e:
        raise HTTPException(429, detail=f"Ограничение Telegram: подождите {e.seconds} секунд перед повторной попыткой")
        
    except Exception as e:
        error_msg = str(e)
        if "PHONE_NOT_OCCUPIED" in error_msg:
            raise HTTPException(400, detail=f"Номер {req.phone} не зарегистрирован в Telegram")
        elif "PHONE_NUMBER_BANNED" in error_msg:
            raise HTTPException(400, detail=f"Номер {req.phone} заблокирован в Telegram")
        elif "PHONE_NUMBER_FLOOD" in error_msg:
            raise HTTPException(429, detail="Слишком много запросов добавления контактов. Подождите некоторое время.")
        else:
            raise HTTPException(500, detail=f"Ошибка добавления контакта: {error_msg}")


# ==================== Остальные эндпоинты (без изменений) ====================
async def incoming_handler(event):
    # Telethon 1.42: у события есть флаг .out, .incoming, а не .is_outgoing
    if getattr(event, "out", False):
        return

    from_account = "unknown"
    for name, cl in ACTIVE_CLIENTS.items():
        if cl.session == event.client.session:
            from_account = name
            break

    payload = {
        "type": "new_message",
        "from_account": from_account,
        "sender_id": event.sender_id,
        "chat_id": event.chat_id,
        "message_id": event.id,
        "text": event.text or "",
        "date": event.date.isoformat() if event.date else None,
    }

    if WEBHOOK_URL:
        try:
            headers = {}
            if WEBHOOK_SECRET:
                headers["X-Webhook-Secret"] = WEBHOOK_SECRET

            # requests - синхронный: уносим в отдельный поток, чтобы не блокировать Telethon loop
            await asyncio.to_thread(
                requests.post,
                WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=12,
            )
        except:
            pass


@app.post("/send")
async def send_message(req: SendMessageReq):
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        await client.send_message(req.chat_id, req.text)
        return {"status": "sent", "from": req.account, "to": req.chat_id}
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка отправки: {str(e)}")


@app.post("/export_members")
async def export_members(req: ExportMembersReq):
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        group = await client.get_entity(req.group)
        participants = await client.get_participants(group, aggressive=True)

        members = []
        for p in participants:
            # Определяем, является ли участник администратором
            is_admin = False
            admin_title = None
            
            # Проверяем разные способы определения администратора
            if hasattr(p, 'participant'):
                # Для участников групп/каналов
                participant = p.participant
                if hasattr(participant, 'admin_rights') and participant.admin_rights:
                    is_admin = True
                    admin_title = getattr(participant, 'rank', None) or getattr(participant, 'title', None)
            
            # Альтернативная проверка через права
            if not is_admin and hasattr(p, 'admin_rights') and p.admin_rights:
                is_admin = True
            
            # Собираем информацию об участнике
            member_data = {
                "id": p.id,
                "username": p.username if hasattr(p, 'username') and p.username else None,
                "first_name": p.first_name if hasattr(p, 'first_name') and p.first_name else "",
                "last_name": p.last_name if hasattr(p, 'last_name') and p.last_name else "",
                "phone": p.phone if hasattr(p, 'phone') and p.phone else None,
                "is_admin": is_admin,
                "admin_title": admin_title,
                "is_bot": p.bot if hasattr(p, 'bot') else False,
                "is_self": p.self if hasattr(p, 'self') else False,
                "is_contact": p.contact if hasattr(p, 'contact') else False,
                "is_mutual_contact": p.mutual_contact if hasattr(p, 'mutual_contact') else False,
                "is_deleted": p.deleted if hasattr(p, 'deleted') else False,
                "is_verified": p.verified if hasattr(p, 'verified') else False,
                "is_restricted": p.restricted if hasattr(p, 'restricted') else False,
                "is_scam": p.scam if hasattr(p, 'scam') else False,
                "is_fake": p.fake if hasattr(p, 'fake') else False,
                "is_support": p.support if hasattr(p, 'support') else False,
                "is_premium": p.premium if hasattr(p, 'premium') else False,
            }
            
            # Добавляем статус (онлайн/офлайн)
            if hasattr(p, 'status'):
                status = p.status
                if hasattr(status, '__class__'):
                    member_data["status"] = status.__class__.__name__
                    if hasattr(status, 'was_online'):
                        member_data["last_seen"] = status.was_online.isoformat() if status.was_online else None
            
            members.append(member_data)

        return {
            "status": "exported",
            "group": req.group,
            "group_title": group.title if hasattr(group, 'title') else "Unknown",
            "total_members": len(members),
            "admins_count": sum(1 for m in members if m["is_admin"]),
            "bots_count": sum(1 for m in members if m["is_bot"]),
            "members": members
        }
    except Exception as e:
        print(f"Ошибка экспорта участников: {e}")
        raise HTTPException(500, detail=f"Ошибка экспорта: {str(e)}")


@app.post("/dialogs")
async def get_dialogs(req: GetDialogsReq):
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        if req.include_folders:
            dialog_list = await get_dialogs_with_folders_info(client, req.limit)
        else:
            dialogs = await client.get_dialogs(limit=req.limit)
            dialog_list = [
                DialogInfo(
                    id=dialog.entity.id,
                    title=dialog.title or dialog.name or "Без названия",
                    username=getattr(dialog.entity, 'username', None),
                    folder_names=[],
                    is_group=getattr(dialog.entity, 'megagroup', False) or getattr(dialog.entity, 'gigagroup', False),
                    is_channel=getattr(dialog.entity, 'broadcast', False),
                    is_user=hasattr(dialog.entity, 'first_name'),
                    unread_count=dialog.unread_count,
                    last_message_date=dialog.date.isoformat() if dialog.date else None
                ) for dialog in dialogs
            ]
        
        return {
            "status": "success",
            "account": req.account,
            "total_dialogs": len(dialog_list),
            "dialogs": dialog_list
        }
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка получения диалогов: {str(e)}")


@app.post("/folders/{account}")
async def get_all_folders(account: str):
    client = ACTIVE_CLIENTS.get(account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {account}")

    try:
        dialog_filters_result = await client(GetDialogFiltersRequest())
        dialog_filters = getattr(dialog_filters_result, 'filters', [])
        folders = []
        
        for folder in dialog_filters:
            folder_title = extract_folder_title(folder)
            
            if hasattr(folder, 'id') and folder_title:
                folder_info = {
                    "id": folder.id,
                    "title": folder_title,
                    "color": getattr(folder, 'color', None),
                    "pinned": getattr(folder, 'pinned', False),
                    "include_count": len(getattr(folder, 'include_peers', [])),
                    "exclude_count": len(getattr(folder, 'exclude_peers', []))
                }
                folders.append(folder_info)
        
        return {
            "status": "success",
            "account": account,
            "total_folders": len(folders),
            "folders": folders
        }
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка получения папок: {str(e)}")


@app.post("/entity_info")
async def get_entity_info(req: EntityInfoReq):
    """
    Получить описание пользователя или группы/канала.
    target может быть username (@channel / @user), числовым id.
    """
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    target = req.target
    if isinstance(target, str):
        t = target.strip()
        if t.startswith('@'):
            t = t[1:]
        if t.lstrip('-').isdigit():
            target = int(t)
        else:
            target = t

    try:
        entity = await client.get_entity(target)
    except Exception as e:
        raise HTTPException(400, detail=f"Не удалось найти объект по идентификатору '{req.target}': {str(e)}")

    if isinstance(entity, User):
        full = await client(GetFullUserRequest(entity.id))
        fu = full.full_user

        user_info = UserInfo(
            id=entity.id,
            username=getattr(entity, "username", None),
            first_name=getattr(entity, "first_name", None),
            last_name=getattr(entity, "last_name", None),
            phone=getattr(entity, "phone", None),
            about=getattr(fu, "about", None),
            is_bot=getattr(entity, "bot", False),
            is_premium=getattr(entity, "premium", False),
            is_verified=getattr(entity, "verified", False),
            is_restricted=getattr(entity, "restricted", False),
            is_scam=getattr(entity, "scam", False),
            is_deleted=getattr(entity, "deleted", False),
        )

        return {
            "status": "success",
            "type": "user",
            "account": req.account,
            "target": req.target,
            "data": user_info,
        }

    if isinstance(entity, Channel):
        full = await client(GetFullChannelRequest(entity))
        fc = full.full_chat

        chat_info = ChatInfo(
            id=entity.id,
            title=getattr(entity, "title", "") or "Без названия",
            username=getattr(entity, "username", None),
            about=getattr(fc, "about", None),
            is_group=bool(getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False)),
            is_channel=bool(getattr(entity, "broadcast", False)),
            participants_count=getattr(fc, "participants_count", None),
            admins_count=getattr(fc, "admins_count", None),
            online_count=getattr(fc, "online_count", None),
        )

        return {
            "status": "success",
            "type": "channel",
            "account": req.account,
            "target": req.target,
            "data": chat_info,
        }

    if isinstance(entity, Chat):
        full = await client(GetFullChatRequest(entity.id))
        fc = full.full_chat

        chat_info = ChatInfo(
            id=entity.id,
            title=getattr(entity, "title", "") or "Без названия",
            username=None,
            about=getattr(fc, "about", None),
            is_group=True,
            is_channel=False,
            participants_count=getattr(fc, "participants_count", None),
            admins_count=getattr(fc, "admins_count", None),
            online_count=getattr(fc, "online_count", None),
        )

        return {
            "status": "success",
            "type": "chat",
            "account": req.account,
            "target": req.target,
            "data": chat_info,
        }

    raise HTTPException(400, detail=f"Тип объекта не поддерживается: {type(entity).__name__}")


@app.post("/entities_info")
async def get_entities_info(req: MultiEntityInfoReq):
    """
    Получить описания сразу для нескольких сущностей (users / группы / каналы).
    """
    results: List[Dict] = []

    for target in req.targets:
        try:
            info = await get_entity_info(EntityInfoReq(account=req.account, target=target))
            results.append({
                "target": target,
                "ok": True,
                "data": info,
            })
        except HTTPException as e:
            results.append({
                "target": target,
                "ok": False,
                "status_code": e.status_code,
                "error": e.detail,
            })
        except Exception as e:
            results.append({
                "target": target,
                "ok": False,
                "status_code": 500,
                "error": str(e),
            })

    return {
        "status": "success",
        "account": req.account,
        "total": len(results),
        "results": results,
    }


def _safe_segment(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    value = value.strip("._-")
    return value or "file"


@app.get("/media/proxy")
async def media_proxy(account: str, chat_id: str, message_id: int):
    """
    Прокси-скачивание файла из Telegram без хранения на диске.
    """
    client = ACTIVE_CLIENTS.get(account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {account}")

    target: str | int = chat_id
    if isinstance(target, str):
        t = target.strip()
        if t.startswith("@"):
            t = t[1:]
        if t.lstrip("-").isdigit():
            target = int(t)
        else:
            target = t

    try:
        chat = await client.get_entity(target)
        msg = await client.get_messages(chat, ids=message_id)
        if not msg:
            raise HTTPException(404, detail="Сообщение не найдено")
        if not getattr(msg, "media", None):
            raise HTTPException(404, detail="В сообщении нет медиа")

        file_obj = getattr(msg, "file", None)
        filename = getattr(file_obj, "name", None) if file_obj else None
        mime = getattr(file_obj, "mime_type", None) if file_obj else None
        if not mime and filename:
            mime = mimetypes.guess_type(filename)[0]
        if not mime:
            mime = "application/octet-stream"

        buf = io.BytesIO()
        downloaded = await client.download_media(msg, file=buf)
        if not downloaded:
            raise HTTPException(500, detail="Не удалось скачать медиа")
        buf.seek(0)

        headers = {}
        if filename:
            safe_name = _safe_segment(filename)
            headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'

        return StreamingResponse(buf, media_type=mime, headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка прокси-скачивания: {str(e)}")


@app.post("/chat_history")
async def get_chat_history(req: GetChatHistoryReq, request: Request):
    client = ACTIVE_CLIENTS.get(req.account)
    if not client:
        raise HTTPException(400, detail=f"Аккаунт не найден: {req.account}")

    try:
        chat_id = req.chat_id
        
        if isinstance(chat_id, str):
            if chat_id.startswith('@'):
                chat_id = chat_id[1:]
            if chat_id.lstrip('-').isdigit():
                chat_id = int(chat_id)
        
        try:
            chat = await client.get_entity(chat_id)
        except Exception:
            dialogs = await client.get_dialogs()
            for dialog in dialogs:
                if str(dialog.id) == str(chat_id) or (hasattr(dialog.entity, 'username') and dialog.entity.username == chat_id):
                    chat = dialog.entity
                    break
            else:
                raise HTTPException(400, detail=f"Не удалось найти чат: {req.chat_id}")
        
        kwargs = {"limit": req.limit}
        if req.offset_id is not None and req.offset_id > 0:
            kwargs["offset_id"] = req.offset_id

        messages = await client.get_messages(chat, **kwargs)
        
        message_list = []
        for msg in messages:
            if msg is None:
                continue
                
            text = ""
            if hasattr(msg, 'text') and msg.text:
                text = msg.text
            elif hasattr(msg, 'message') and msg.message:
                text = msg.message
            
            has_media = hasattr(msg, 'media') and msg.media is not None
            media_type = msg.media.__class__.__name__ if has_media else None
            
            if not text and not has_media:
                continue
            
            media_url = None
            media_filename = None
            media_mime = None

            if req.include_media and has_media:
                try:
                    file_obj = getattr(msg, "file", None)
                    media_filename = getattr(file_obj, "name", None) if file_obj else None
                    media_mime = getattr(file_obj, "mime_type", None) if file_obj else None
                    base = str(request.base_url).rstrip("/")
                    media_url = f"{base}/media/proxy?account={req.account}&chat_id={req.chat_id}&message_id={msg.id}"
                except Exception:
                    media_url = None
                    media_filename = None
                    media_mime = None

            message = ChatMessage(
                id=msg.id,
                date=msg.date.isoformat() if msg.date else "",
                from_id=None,
                text=text,
                is_outgoing=msg.out if hasattr(msg, 'out') else False,
                has_media=has_media if req.include_media else False,
                media_type=media_type if req.include_media else None,
                media_url=media_url,
                media_filename=media_filename,
                media_mime=media_mime,
            )
            message_list.append(message)
        
        chat_title = "Unknown"
        if hasattr(chat, 'title'):
            chat_title = chat.title
        elif hasattr(chat, 'first_name'):
            chat_title = chat.first_name
            if hasattr(chat, 'last_name') and chat.last_name:
                chat_title += f" {chat.last_name}"
        
        return {
            "status": "success",
            "account": req.account,
            "chat_id": req.chat_id,
            "chat_title": chat_title,
            "total_messages": len(message_list),
            "messages": message_list
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка получения истории: {str(e)}")


# ==================== Запуск ====================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("telegram_bot:app", host="0.0.0.0", port=port, reload=False)



