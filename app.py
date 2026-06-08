from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime, timedelta, timezone
import os
import uuid
import random
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = "auction_secret"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get(
    "DATABASE_PATH",
    os.path.join(BASE_DIR, "database.db")
)
TAIWAN_TIMEZONE = timezone(timedelta(hours=8))

# 管理員後台二次驗證密碼。
# 本機預設為 admin123；正式部署請在 Render/Railway 設定環境變數 ADMIN_PANEL_PASSWORD。
ADMIN_PANEL_PASSWORD = os.environ.get("ADMIN_PANEL_PASSWORD", "admin123")

socketio = SocketIO(
    app,
    async_mode="eventlet",
    cors_allowed_origins="*"
)
online_users = {}

UPLOAD_FOLDER = os.environ.get(
    "UPLOAD_FOLDER",
    os.path.join(BASE_DIR, "static", "uploads")
)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


NPC_PERSONAS = {
    "toxic": {
        "username": "🤡 毒舌小丑",
        "lines": [
            "這價格也太養生，現場是在等打折嗎？",
            "這商品都熱起來了，還有人在旁邊裝冷靜。",
            "收藏不出價的人最多，真正敢喊的才是狠人。",
            "這價格不是不能買，是不夠有態度。",
            "現在喊價還算溫柔，等等尾刀才是真的髒。",
        ],
    },
    "hype": {
        "username": "🔥 拱火仔",
        "lines": [
            "剛剛那一口有點狠，下一位敢不敢跟？",
            "火藥味出來了，這才叫拍賣。",
            "別讓他太舒服得標，現場給點壓力。",
            "有人已經上頭了，我聞到戰場味了。",
            "價格開始動了，聊天室不要只看戲。",
        ],
    },
    "sniper": {
        "username": "🎯 尾刀預言家",
        "lines": [
            "最後幾秒一定有人偷襲，我先卡位看戲。",
            "現在領先不代表安全，尾刀仔都還沒醒。",
            "這局還沒結束，真正狠的都在等倒數。",
            "別高興太早，最後 10 秒才是真正的地獄。",
            "我敢賭等等有人壓秒出價。",
        ],
    },
    "pro": {
        "username": "🧠 行情分析師",
        "lines": [
            "以目前熱度來看，這價格還沒到極限。",
            "瀏覽跟收藏都有動，這商品應該還會被追。",
            "現在價格偏保守，真正想買可以考慮先卡位。",
            "賣家評價不差，這件商品有繼續拉高的空間。",
            "如果是熱門分類，這個價格其實還能再衝。",
        ],
    },
    "ghost": {
        "username": "👻 深夜鬼王",
        "lines": [
            "這時間還在競標的，都不是普通人。",
            "深夜場開始有那個味了。",
            "安靜不代表沒人，尾刀仔都躲在暗處。",
            "半夜下標最可怕，因為大家都已經不理性了。",
            "現在的聊天室安靜得像暴風雨前。",
        ],
    },
}

FINAL_NPC_PERSONAS = {
    "sniper": {
        "username": "🎯 尾刀預言家",
        "lines": [
            "最後 30 秒，現在才是真正的戰場。",
            "尾刀仔差不多該醒了。",
            "這時間點還不出手，等等只能拍大腿。",
            "領先的人不要笑太早，最後一刀最痛。",
            "壓秒怪準備進場，我已經看到劇本了。",
        ],
    },
    "hype": {
        "username": "🔥 拱火仔",
        "lines": [
            "最後倒數，誰縮誰尷尬。",
            "現在不衝，這局就被帶走了。",
            "全場看著你們，敢不敢再補一口？",
            "最後關頭才好看，拜託不要冷掉。",
            "這局如果沒人偷襲，我會失望。",
        ],
    },
}

HIGH_PRICE_NPC_PERSONAS = {
    "pro": {
        "username": "🧠 行情分析師",
        "lines": [
            "價格破到這裡，代表現場真的有人想拿。",
            "這不是亂喊了，這是認真局。",
            "高價區間開始了，接下來每一口都很關鍵。",
        ],
    },
    "toxic": {
        "username": "🤡 毒舌小丑",
        "lines": [
            "有人開始認真了，剛剛那些觀望的還在裝睡？",
            "這價格才像有在玩，前面都只是暖身。",
            "喊到這裡還敢跟的，是真的有料。",
        ],
    },
}

npc_last_emit_time = {}


def choose_npc_persona(current_price=None, end_time_text=None):
    """依照價格、倒數、時間選出 NPC 人格。"""

    now = datetime.now(TAIWAN_TIMEZONE)
    persona_pool = NPC_PERSONAS

    try:
        if end_time_text:
            end_time = datetime.strptime(end_time_text, "%Y-%m-%d %H:%M:%S")
            seconds_left = (
                end_time
                - now.replace(tzinfo=None)
            ).total_seconds()

            if 0 <= seconds_left <= 30:
                persona_pool = FINAL_NPC_PERSONAS
    except Exception:
        pass

    try:
        if current_price is not None and int(current_price) >= 10000:
            persona_pool = HIGH_PRICE_NPC_PERSONAS
    except Exception:
        pass

    # 深夜 00:00 ~ 05:00 提高鬼王出現率
    if now.hour >= 0 and now.hour < 5 and persona_pool == NPC_PERSONAS:
        if random.random() < 0.45:
            return NPC_PERSONAS["ghost"]

    key = random.choice(list(persona_pool.keys()))
    return persona_pool[key]


def emit_npc_message(product_id, current_price=None, end_time_text=None, chance=0.3):
    """讓商品聊天室偶爾出現人格型 NPC 拱火訊息，避免洗頻。"""

    product_id = str(product_id)
    now = datetime.now(TAIWAN_TIMEZONE)

    last_emit = npc_last_emit_time.get(product_id)
    if last_emit and (now - last_emit).total_seconds() < 60:
        return

    if random.random() >= chance:
        return

    persona = choose_npc_persona(
        current_price=current_price,
        end_time_text=end_time_text
    )

    npc_last_emit_time[product_id] = now

    socketio.emit(
        "new_message",
        {
            "username": persona["username"],
            "message": random.choice(persona["lines"]),
            "time": now.strftime("%H:%M:%S")
        },
        room=product_id
    )


def get_bid_increment(current_price):
    """依目前價格決定每次最低加價幅度。"""

    try:
        current_price = int(current_price)
    except Exception:
        current_price = 0

    if current_price < 1000:
        return 10

    if current_price < 10000:
        return 50

    return 100



def is_product_ended(product):
    """判斷商品是否已結標。結標後禁止一般出價與自動出價。"""

    if product is None:
        return True

    try:
        end_time = datetime.strptime(
            product["end_time"],
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return False

    now = datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)

    return now >= end_time


def save_uploaded_image(file_storage):
    """儲存上傳圖片，回傳 /static/uploads/... 路徑。"""

    if not file_storage or file_storage.filename == "":
        return None

    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    filename = f"{uuid.uuid4()}.{ext}"
    file_storage.save(
        os.path.join(
            app.config["UPLOAD_FOLDER"],
            filename
        )
    )

    return "/static/uploads/" + filename



def get_product_images(product):
    """回傳商品多圖片清單，舊資料只有 image 也能相容。"""

    images = []

    if product is None:
        return images

    try:
        keys = product.keys()
    except Exception:
        keys = []

    for field in ["image", "image1", "image2", "image3", "image4", "image5"]:
        if field in keys and product[field]:
            if product[field] not in images:
                images.append(product[field])

    return images


def get_user_level(product_count=0, sold_count=0, bid_count=0):
    """依使用者行為產生等級稱號。"""

    score = int(product_count or 0) * 2 + int(sold_count or 0) * 5 + int(bid_count or 0)

    if score >= 200:
        return {
            "name": "S級 校園傳奇",
            "badge": "💎",
            "score": score
        }

    if score >= 100:
        return {
            "name": "A級 交易高手",
            "badge": "🏆",
            "score": score
        }

    if score >= 40:
        return {
            "name": "B級 活躍玩家",
            "badge": "🔥",
            "score": score
        }

    if score >= 10:
        return {
            "name": "C級 校園賣家",
            "badge": "🥈",
            "score": score
        }

    return {
        "name": "新手會員",
        "badge": "🌱",
        "score": score
    }


def emit_realtime_notification(user_id, message, link="/notifications"):
    """送出資料庫通知並用 Socket.IO 即時推播給指定使用者。"""

    if user_id is None:
        return

    socketio.emit(
        "realtime_notification",
        {
            "message": message,
            "link": link,
            "time": datetime.now(TAIWAN_TIMEZONE).strftime("%H:%M:%S")
        },
        room=f"user_{user_id}"
    )


def create_notification_realtime(user_id, message, link):
    """同時建立通知與即時推播。"""

    create_notification(user_id, message, link)
    emit_realtime_notification(user_id, message, link)


def get_chat_reaction_counts(message_id):
    """取得單一聊天室訊息的反應統計。"""

    db = get_db()

    rows = db.execute(
        """
        SELECT reaction, COUNT(*) AS count
        FROM chat_reactions
        WHERE message_id=?
        GROUP BY reaction
        """,
        (message_id,)
    ).fetchall()

    db.close()

    result = {
        "👍": 0,
        "😂": 0,
        "🔥": 0
    }

    for r in rows:
        result[r["reaction"]] = r["count"]

    return result


def get_db():
    conn = sqlite3.connect(
        DATABASE,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def now_text():
    return datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def ensure_admin_feature_columns(db):
    """後台進階功能需要的欄位與資料表，舊資料庫也能自動補齊。"""

    for sql in [
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN ban_reason TEXT",
        "ALTER TABLE users ADD COLUMN admin_role TEXT DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN has_received_listing_bonus INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN admin_hidden INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN admin_hidden_reason TEXT",
        "ALTER TABLE products ADD COLUMN admin_hidden_at TEXT"
    ]:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS coin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            created_at TEXT
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )


def add_coin_log(db, user_id, amount, reason):
    """記錄金幣增加或扣除。amount 可為正數或負數。"""

    ensure_admin_feature_columns(db)
    db.execute(
        """
        INSERT INTO coin_logs(user_id, amount, reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            amount,
            reason,
            now_text()
        )
    )


# 放這裡 ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓

def create_reports_table():

    db = get_db()

    # 基礎資料表保底：避免新環境沒有先跑 init_db.py 造成 ALTER / SELECT 爆掉
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            email TEXT,
            password TEXT,
            coins INTEGER DEFAULT 100,
            is_admin INTEGER DEFAULT 0,
            admin_role TEXT DEFAULT 'user',
            has_received_listing_bonus INTEGER DEFAULT 0
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            start_price INTEGER,
            current_price INTEGER,
            image TEXT,
            description TEXT,
            category TEXT,
            end_time TEXT,
            user_id INTEGER,
            views INTEGER DEFAULT 0
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            user_id INTEGER,
            bid_price INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            user_id INTEGER,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            blocked_user_id INTEGER,
            blocked_by INTEGER,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            reviewer_id INTEGER,
            target_user_id INTEGER,
            rating INTEGER,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS auto_bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            user_id INTEGER,
            max_price INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            reporter_id INTEGER,
            reason TEXT,
            created_at TEXT
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            discount INTEGER,
            is_used INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            description TEXT,
            created_at TEXT
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reset_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            code TEXT,
            is_used INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )

    # 商品置頂欄位：如果已存在會自動略過
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN is_pinned INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN pin_expire_time TEXT"
        )
    except sqlite3.OperationalError:
        pass

    # 商品特殊框欄位：如果已存在會自動略過
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN special_frame INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # 完成交易欄位：1 = 已完成交易並從首頁下架
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN is_completed INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # 校園面交地點欄位
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN meet_location TEXT"
        )
    except sqlite3.OperationalError:
        pass

    # 使用者頭像欄位
    try:
        db.execute(
            "ALTER TABLE users ADD COLUMN avatar TEXT"
        )
    except sqlite3.OperationalError:
        pass

    # 使用者封面欄位
    try:
        db.execute(
            "ALTER TABLE users ADD COLUMN banner TEXT"
        )
    except sqlite3.OperationalError:
        pass

    # 首次上架獎勵欄位：1 = 已領過首次上架 1000 金幣
    try:
        db.execute(
            "ALTER TABLE users ADD COLUMN has_received_listing_bonus INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # 商品暫停販售欄位：1 = 暫停顯示與競標
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN is_paused INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # 商品多圖片欄位
    for image_column in ["image1", "image2", "image3", "image4", "image5"]:
        try:
            db.execute(
                f"ALTER TABLE products ADD COLUMN {image_column} TEXT"
            )
        except sqlite3.OperationalError:
            pass

    # 商品狀態欄位：全新、九成新、二手、故障品
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN condition TEXT DEFAULT '二手'"
        )
    except sqlite3.OperationalError:
        pass


    # 商品規格欄位
    for spec_column in [
        "trade_method TEXT DEFAULT '面交'",
        "meetup_time TEXT DEFAULT '平日晚上'",
        "payment_method TEXT DEFAULT '現金'"
    ]:
        try:
            db.execute(
                f"ALTER TABLE products ADD COLUMN {spec_column}"
            )
        except sqlite3.OperationalError:
            pass



    db.execute(
    """
    CREATE TABLE IF NOT EXISTS private_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        product_id INTEGER,
        message TEXT,
        image TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TEXT
    )
    """
)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS browsing_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            viewed_at TEXT
        )
        """
    )


    db.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            created_at TEXT
        )
        """
    )


    db.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            user_id INTEGER,
            reaction TEXT,
            created_at TEXT,
            UNIQUE(message_id, user_id, reaction)
        )
        """
    )


    # 即時倒數提醒：避免同一商品重複提醒
    try:
        db.execute("ALTER TABLE products ADD COLUMN ending_reminder_sent INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # 檢舉審核系統
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            reporter_id INTEGER,
            reason TEXT,
            status TEXT DEFAULT '待審核',
            admin_note TEXT,
            created_at TEXT
        )
        """
    )
    for report_column in ["status TEXT DEFAULT '待審核'", "admin_note TEXT"]:
        try:
            db.execute(f"ALTER TABLE reports ADD COLUMN {report_column}")
        except sqlite3.OperationalError:
            pass

    # 站內交易訂單
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER UNIQUE,
            seller_id INTEGER,
            buyer_id INTEGER,
            final_price INTEGER,
            status TEXT DEFAULT '等待面交',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # 站內付款功能：模擬付款紀錄，適合專題展示使用
    for payment_column in [
        "payment_status TEXT DEFAULT '未付款'",
        "payment_method TEXT",
        "payment_note TEXT",
        "paid_at TEXT"
    ]:
        try:
            db.execute(f"ALTER TABLE trade_orders ADD COLUMN {payment_column}")
        except sqlite3.OperationalError:
            pass

    # 商品公開問答區
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS product_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            asker_id INTEGER,
            question TEXT,
            answer TEXT,
            created_at TEXT,
            answered_at TEXT
        )
        """
    )


    # 後台進階功能：停權、商品下架、金幣紀錄、公告
    ensure_admin_feature_columns(db)

    db.commit()
    db.close()


def create_notification(user_id, message, link):
    db = get_db()

    db.execute(
        """
        INSERT INTO notifications(user_id, message, link, is_read, created_at)
        VALUES (?, ?, ?, 0, ?)
        """,
        (
            user_id,
            message,
            link,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    db.commit()
    db.close()






def notify_ending_soon_products():
    """通知收藏者與出價者：商品 10 分鐘內即將結標。"""
    db = get_db()
    now_text = datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

    products = db.execute(
        """
        SELECT *
        FROM products
        WHERE IFNULL(is_completed, 0)=0
        AND IFNULL(is_paused, 0)=0
        AND IFNULL(ending_reminder_sent, 0)=0
        AND datetime(end_time) > datetime('now', '+8 hours')
        AND datetime(end_time) <= datetime('now', '+8 hours', '+10 minutes')
        """
    ).fetchall()

    for product in products:
        users = db.execute(
            """
            SELECT DISTINCT user_id
            FROM (
                SELECT user_id FROM favorites WHERE product_id=?
                UNION
                SELECT user_id FROM bids WHERE product_id=?
            )
            WHERE user_id IS NOT NULL
            """,
            (product["id"], product["id"])
        ).fetchall()

        for user in users:
            create_notification_realtime(
                user["user_id"],
                f"⏰ 你關注的商品即將在 10 分鐘內結標：{product['title']}",
                f"/product/{product['id']}"
            )

        db.execute(
            "UPDATE products SET ending_reminder_sent=1 WHERE id=?",
            (product["id"],)
        )

    db.commit()
    db.close()


def ending_reminder_background_task():
    """背景每 60 秒檢查一次即將結標商品。"""
    while True:
        try:
            notify_ending_soon_products()
        except Exception as exc:
            print("ending reminder error:", exc)
        socketio.sleep(60)


def create_trade_order_if_needed(product_id):
    """商品結標後，自動為最高出價者建立交易訂單。"""
    db = get_db()

    product = db.execute(
        "SELECT * FROM products WHERE id=?",
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return None

    try:
        end_time = datetime.strptime(product["end_time"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        db.close()
        return None

    if datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None) <= end_time:
        db.close()
        return None

    existing = db.execute(
        "SELECT * FROM trade_orders WHERE product_id=?",
        (product_id,)
    ).fetchone()

    if existing:
        db.close()
        return existing

    winner = db.execute(
        """
        SELECT * FROM bids
        WHERE product_id=?
        ORDER BY bid_price DESC
        LIMIT 1
        """,
        (product_id,)
    ).fetchone()

    if winner is None:
        db.close()
        return None

    now_text = datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """
        INSERT INTO trade_orders(product_id, seller_id, buyer_id, final_price, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, '等待面交', ?, ?)
        """,
        (product_id, product["user_id"], winner["user_id"], winner["bid_price"], now_text, now_text)
    )
    db.commit()

    order = db.execute(
        "SELECT * FROM trade_orders WHERE product_id=?",
        (product_id,)
    ).fetchone()

    db.close()

    create_notification_realtime(
        product["user_id"],
        f"📦 你的商品已結標並產生交易訂單：{product['title']}",
        f"/product/{product_id}"
    )
    create_notification_realtime(
        winner["user_id"],
        f"🎉 你已得標並產生交易訂單：{product['title']}",
        f"/product/{product_id}"
    )

    return order

def send_reset_code_email(email, code):
    """
    寄出忘記密碼驗證碼。

    如果沒有設定環境變數 MAIL_USERNAME / MAIL_PASSWORD，
    系統會進入專題展示模式：不寄信，但驗證碼仍會存在資料庫並顯示在頁面。
    """
    mail_username = os.environ.get("MAIL_USERNAME")
    mail_password = os.environ.get("MAIL_PASSWORD")
    mail_server = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    mail_port = int(os.environ.get("MAIL_PORT", "587"))

    if not mail_username or not mail_password:
        return False

    subject = "校園拍賣平台密碼重設驗證碼"
    body = f"""你的密碼重設驗證碼是：{code}

此驗證碼 10 分鐘內有效。
如果不是你本人操作，請忽略此信件。
"""

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = mail_username
    msg["To"] = email

    with smtplib.SMTP(mail_server, mail_port) as server:
        server.starttls()
        server.login(mail_username, mail_password)
        server.send_message(msg)

    return True


def get_reset_code_age_minutes(created_at_text):
    created_at = datetime.strptime(
        created_at_text,
        "%Y-%m-%d %H:%M:%S"
    )

    now = datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)

    return (now - created_at).total_seconds() / 60


def unlock_achievement(user_id, title, description):

    if user_id is None:
        return

    db = get_db()

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            description TEXT,
            created_at TEXT
        )
        """
    )

    existing = db.execute(
        """
        SELECT *
        FROM achievements
        WHERE user_id=?
        AND title=?
        """,
        (
            user_id,
            title
        )
    ).fetchone()

    if existing:
        db.close()
        return

    db.execute(
        """
        INSERT INTO achievements(
            user_id,
            title,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            title,
            description,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    db.commit()
    db.close()


def check_all_achievements(user_id):

    if user_id is None:
        return

    db = get_db()

    # 第一次下標 / 競標達人 / 出價王

    bid_stats = db.execute(
        """
        SELECT
            COUNT(*) AS bid_count,
            IFNULL(MAX(bid_price), 0) AS max_bid
        FROM bids
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    if bid_stats["bid_count"] >= 1:
        unlock_achievement(
            user_id,
            "🏆 第一次下標",
            "完成第一次商品競標"
        )

    if bid_stats["bid_count"] >= 50:
        unlock_achievement(
            user_id,
            "🔥 競標達人",
            "累積競標達到 50 次"
        )

    if bid_stats["max_bid"] >= 10000:
        unlock_achievement(
            user_id,
            "💰 出價王",
            "最高出價超過 10000"
        )

    # 第一次收藏 / 收藏王

    favorite_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM favorites
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()["count"]

    if favorite_count >= 1:
        unlock_achievement(
            user_id,
            "🏆 第一次收藏",
            "收藏了第一件商品"
        )

    if favorite_count >= 100:
        unlock_achievement(
            user_id,
            "❤️ 收藏王",
            "收藏超過 100 件商品"
        )

    # 上架達人 / 超人氣賣家

    seller_stats = db.execute(
        """
        SELECT
            COUNT(*) AS product_count,
            IFNULL(SUM(views), 0) AS total_views
        FROM products
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    if seller_stats["product_count"] >= 30:
        unlock_achievement(
            user_id,
            "📦 上架達人",
            "上架商品達到 30 件"
        )

    if seller_stats["total_views"] >= 1000:
        unlock_achievement(
            user_id,
            "🚀 超人氣賣家",
            "商品總瀏覽達到 1000 次"
        )

    # 五星賣家

    review_stats = db.execute(
        """
        SELECT
            IFNULL(AVG(rating), 0) AS avg_rating,
            COUNT(*) AS review_count
        FROM reviews
        WHERE target_user_id=?
        """,
        (user_id,)
    ).fetchone()

    if (
        review_stats["review_count"] >= 1
        and review_stats["avg_rating"] >= 5
    ):
        unlock_achievement(
            user_id,
            "⭐ 五星賣家",
            "賣家評價達到 5.0 顆星"
        )

    # 首次成交：曾經上架商品且有人得標，或自己曾得標

    sold = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM products
        JOIN bids
        ON products.id = bids.product_id
        WHERE products.user_id=?
        GROUP BY products.id
        """,
        (user_id,)
    ).fetchall()

    won = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM bids
        JOIN products
        ON bids.product_id = products.id
        WHERE bids.user_id=?
        AND bids.bid_price = products.current_price
        AND datetime(products.end_time) < datetime('now', '+8 hours')
        """,
        (user_id,)
    ).fetchone()["count"]

    if len(sold) >= 1 or won >= 1:
        unlock_achievement(
            user_id,
            "🏆 首次成交",
            "完成第一筆成功交易"
        )

    db.close()


def get_recommended_products(user_id):

    db = get_db()

    products = db.execute(
        """
        SELECT
            products.*,
            COUNT(favorites.id) AS favorite_count,
            CASE
                WHEN datetime(products.end_time) <= datetime('now', '+8 hours', '+1 hour')
                THEN 1
                ELSE 0
            END AS is_ending_soon
        FROM products

        LEFT JOIN bids
        ON products.id = bids.product_id

        LEFT JOIN favorites
        ON products.id = favorites.product_id

        WHERE products.user_id != ?
        AND IFNULL(products.is_completed, 0)=0
        AND IFNULL(products.is_paused, 0)=0
        AND IFNULL(products.admin_hidden, 0)=0

        AND (
            products.category IN (

                SELECT category
                FROM products
                WHERE id IN (

                    SELECT product_id
                    FROM bids
                    WHERE user_id=?

                    UNION

                    SELECT product_id
                    FROM favorites
                    WHERE user_id=?
                )
            )
        )

        GROUP BY products.id
        ORDER BY products.views DESC, products.id DESC
        LIMIT 4
        """,
        (
            user_id,
            user_id,
            user_id
        )
    ).fetchall()

    # 如果沒有推薦商品 → 顯示熱門商品

    if not products:

        products = db.execute(
            """
            SELECT
                products.*,
                COUNT(favorites.id) AS favorite_count,
                CASE
                    WHEN datetime(products.end_time) <= datetime('now', '+8 hours', '+1 hour')
                    THEN 1
                    ELSE 0
                END AS is_ending_soon
            FROM products
            LEFT JOIN favorites
            ON products.id = favorites.product_id
            WHERE IFNULL(products.is_completed, 0)=0
            AND IFNULL(products.is_paused, 0)=0
            GROUP BY products.id
            ORDER BY products.views DESC, products.id DESC
            LIMIT 4
            """
        ).fetchall()

    db.close()

    return products
def inject_notification_count():
    
    if "user_id" not in session:
        return dict(notification_count=0)

    db = get_db()

    count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM notifications
        WHERE user_id=? AND is_read=0
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    db.close()

    return dict(notification_count=count)
app.context_processor(inject_notification_count)


def inject_active_announcements():
    try:
        db = get_db()
        ensure_admin_feature_columns(db)
        announcements = db.execute(
            """
            SELECT *
            FROM announcements
            WHERE IFNULL(is_active, 1)=1
            ORDER BY id DESC
            LIMIT 3
            """
        ).fetchall()
        db.close()
        return dict(active_announcements=announcements)
    except Exception:
        return dict(active_announcements=[])

app.context_processor(inject_active_announcements)


@app.route("/")
def index():
    keyword = request.args.get("keyword", "")
    category = request.args.get("category", "")
    sort = request.args.get("sort", "newest")

    db = get_db()

    # 自動取消過期置頂商品
    db.execute(
        """
        UPDATE products
        SET is_pinned=0,
            pin_expire_time=NULL
        WHERE is_pinned=1
        AND pin_expire_time IS NOT NULL
        AND datetime(pin_expire_time) < datetime('now', '+8 hours')
        """
    )
    db.commit()

    sql = """
        SELECT
            products.*,
            COUNT(favorites.id) AS favorite_count,
            CASE
                WHEN datetime(products.end_time) <= datetime('now', '+8 hours', '+1 hour')
                THEN 1
                ELSE 0
            END AS is_ending_soon
        FROM products
        LEFT JOIN favorites
        ON products.id = favorites.product_id
        WHERE 1=1
        AND IFNULL(products.is_completed, 0)=0
        AND IFNULL(products.is_paused, 0)=0
        AND IFNULL(products.admin_hidden, 0)=0
    """
    params = []

    if keyword:
        sql += " AND products.title LIKE ?"
        params.append("%" + keyword + "%")

    if category:
        sql += " AND products.category=?"
        params.append(category)

    sql += " GROUP BY products.id"

    if sort == "views_high":
        sql += """
        ORDER BY
            products.is_pinned DESC,
            products.views DESC
        """
    elif sort == "views_low":
        sql += """
        ORDER BY
            products.is_pinned DESC,
            products.views ASC
        """
    elif sort == "price_high":
        sql += """
        ORDER BY
            products.is_pinned DESC,
            products.current_price DESC
        """
    elif sort == "price_low":
        sql += """
        ORDER BY
            products.is_pinned DESC,
            products.current_price ASC
        """
    elif sort == "oldest":
        sql += """
        ORDER BY
            products.is_pinned DESC,
            products.id ASC
        """
    else:
        sql += """
        ORDER BY
            products.is_pinned DESC,
            products.id DESC
        """

    products = db.execute(sql, params).fetchall()

    recommended_products = []
    recent_viewed_products = []

    if "user_id" in session:
        recommended_products = get_recommended_products(session["user_id"])

        recent_viewed_products = db.execute(
            """
            SELECT
                products.*,
                COUNT(favorites.id) AS favorite_count,
                MAX(browsing_history.viewed_at) AS last_viewed_at,
                CASE
                    WHEN datetime(products.end_time) <= datetime('now', '+8 hours', '+1 hour')
                    THEN 1
                    ELSE 0
                END AS is_ending_soon
            FROM browsing_history
            JOIN products
            ON browsing_history.product_id = products.id
            LEFT JOIN favorites
            ON products.id = favorites.product_id
            WHERE browsing_history.user_id=?
            AND IFNULL(products.is_completed, 0)=0
            AND IFNULL(products.is_paused, 0)=0
            AND IFNULL(products.admin_hidden, 0)=0
            GROUP BY products.id
            ORDER BY last_viewed_at DESC
            LIMIT 8
            """,
            (session["user_id"],)
        ).fetchall()

    db.close()

    return render_template(
        "index.html",
        products=products,
        recommended_products=recommended_products,
        recent_viewed_products=recent_viewed_products,
        category=category,
        keyword=keyword,
        sort=sort
    )

@app.route("/messages/<int:receiver_id>", methods=["GET", "POST"])
def private_chat(receiver_id):
    if "user_id" not in session:
        return redirect("/login")

    product_id = request.args.get("product_id")

    db = get_db()

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        image_file = request.files.get("image")
        image_path = None

        if image_file and image_file.filename != "":
            ext = image_file.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            image_file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            image_path = "/static/uploads/" + filename

        if message or image_path:
            db.execute("""
                INSERT INTO private_messages(
                    sender_id, receiver_id, product_id,
                    message, image, is_read, created_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?)
            """, (
                session["user_id"],
                receiver_id,
                product_id,
                message,
                image_path,
                datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            ))

            db.commit()

            create_notification_realtime(
                receiver_id,
                f"{session['username']} 傳送了一則私訊給你",
                f"/messages/{session['user_id']}"
            )

            return redirect(f"/messages/{receiver_id}?product_id={product_id}")

    db.execute("""
        UPDATE private_messages
        SET is_read=1
        WHERE sender_id=? AND receiver_id=?
    """, (receiver_id, session["user_id"]))

    messages = db.execute("""
        SELECT private_messages.*, users.username AS sender_name
        FROM private_messages
        JOIN users ON private_messages.sender_id = users.id
        WHERE
            (sender_id=? AND receiver_id=?)
            OR
            (sender_id=? AND receiver_id=?)
        ORDER BY private_messages.id ASC
    """, (
        session["user_id"], receiver_id,
        receiver_id, session["user_id"]
    )).fetchall()

    receiver = db.execute(
        "SELECT * FROM users WHERE id=?",
        (receiver_id,)
    ).fetchone()

    product = None

    if product_id:
        product = db.execute(
            "SELECT * FROM products WHERE id=?",
            (product_id,)
        ).fetchone()

    db.commit()
    db.close()

    return render_template(
        "private_chat.html",
        messages=messages,
        receiver=receiver,
        product=product
    )

@app.route("/messages")
def message_list():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    db = get_db()

    chats = db.execute(
        """
        SELECT
            u.id AS other_user_id,
            u.username AS other_username,

            pm.message AS last_message,
            pm.image AS last_image,
            pm.created_at AS last_time,

            (
                SELECT COUNT(*)
                FROM private_messages unread
                WHERE unread.sender_id = u.id
                AND unread.receiver_id = ?
                AND unread.is_read = 0
            ) AS unread_count

        FROM users u

        JOIN private_messages pm
        ON (
            (pm.sender_id = u.id AND pm.receiver_id = ?)
            OR
            (pm.receiver_id = u.id AND pm.sender_id = ?)
        )

        WHERE pm.id IN (
            SELECT MAX(id)
            FROM private_messages
            WHERE sender_id = ?
            OR receiver_id = ?
            GROUP BY
                CASE
                    WHEN sender_id = ? THEN receiver_id
                    ELSE sender_id
                END
        )

        ORDER BY pm.id DESC
        """,
        (
            user_id,
            user_id,
            user_id,
            user_id,
            user_id,
            user_id
        )
    ).fetchall()

    db.close()

    return render_template(
        "messages.html",
        chats=chats
    )

@app.route("/report/<int:product_id>", methods=["POST"])
def report_product(product_id):

    if "user_id" not in session:
        return redirect("/login")

    reason = request.form["reason"]

    db = get_db()

    existing = db.execute(
        """
        SELECT *
        FROM reports
        WHERE product_id=? AND reporter_id=?
        """,
        (
            product_id,
            session["user_id"]
        )
    ).fetchone()

    if existing:
        db.close()

        flash("你已經檢舉過此商品", "danger")
        return redirect(f"/product/{product_id}")

    db.execute(
        """
        INSERT INTO reports(
            product_id,
            reporter_id,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            product_id,
            session["user_id"],
            reason,
            datetime.now(TAIWAN_TIMEZONE).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    db.commit()
    db.close()

    flash("⚠️ 檢舉已送出", "success")

    return redirect(f"/product/{product_id}")

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]
        email = request.form["email"]

        # 校園信箱驗證

        if not email.endswith("@gm.student.ncut.edu.tw"):

            flash(
                "請使用勤益科大信箱註冊",
                "danger"
            )

            return redirect("/register")

        db = get_db()

        existing_user = db.execute(
            """
            SELECT *
            FROM users
            WHERE username=?
            """,
            (username,)
        ).fetchone()

        if existing_user:

            db.close()

            flash("帳號已存在", "danger")

            return redirect("/register")

        cursor = db.execute(
            """
            INSERT INTO users(
                username,
                password,
                email,
                coins
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                username,
                password,
                email,
                2000
            )
        )

        add_coin_log(db, cursor.lastrowid, 2000, "註冊獎勵")

        db.commit()
        db.close()

        flash(
            "🎉 註冊成功！已獲得 2000 金幣",
            "success"
        )

        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()

        user = db.execute(
            """
            SELECT * FROM users
            WHERE username=? AND password=?
            """,
            (
                request.form["username"],
                request.form["password"]
            )
        ).fetchone()

        db.close()

        if user:
            if "is_banned" in user.keys() and int(user["is_banned"] or 0) == 1:
                flash("帳號已被停權，請聯絡管理員", "danger")
                return redirect("/login")

            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["coins"] = user["coins"]

            # 讓右上角頭像可以在登入後直接讀到
            if "avatar" in user.keys() and user["avatar"]:
                session["avatar"] = user["avatar"]
            else:
                session["avatar"] = "/static/default-avatar.png"

            # 讓個人封面也同步到 session
            if "banner" in user.keys() and user["banner"]:
                session["banner"] = user["banner"]
            else:
                session["banner"] = "/static/default-banner.png"

            if "is_admin" in user.keys():
                session["is_admin"] = user["is_admin"]
            else:
                session["is_admin"] = 0

            if "admin_role" in user.keys() and user["admin_role"]:
                session["admin_role"] = user["admin_role"]
            elif session["is_admin"] == 1:
                session["admin_role"] = "super_admin"
            else:
                session["admin_role"] = "user"

            return redirect("/")

        flash("登入失敗，帳號或密碼錯誤", "danger")
        return redirect("/login")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form.get("email", "").strip()

        if email == "":
            flash("請輸入信箱", "danger")
            return redirect("/forgot-password")

        db = get_db()

        user = db.execute(
            """
            SELECT *
            FROM users
            WHERE email=?
            """,
            (email,)
        ).fetchone()

        if user is None:
            db.close()
            flash("找不到此信箱，請確認是否為註冊信箱", "danger")
            return redirect("/forgot-password")

        code = str(random.randint(100000, 999999))
        created_at = datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            """
            INSERT INTO reset_codes(
                email,
                code,
                is_used,
                created_at
            )
            VALUES (?, ?, 0, ?)
            """,
            (
                email,
                code,
                created_at
            )
        )

        db.commit()
        db.close()

        session["reset_email"] = email

        try:
            email_sent = send_reset_code_email(email, code)
        except Exception:
            email_sent = False

        if email_sent:
            flash("驗證碼已寄出，請到信箱查看", "success")
        else:
            flash(f"展示模式：驗證碼是 {code}", "success")

        return redirect("/verify-code")

    return render_template("forgot_password.html")


@app.route("/verify-code", methods=["GET", "POST"])
def verify_code():

    if "reset_email" not in session:
        flash("請先輸入註冊信箱", "danger")
        return redirect("/forgot-password")

    if request.method == "POST":

        code = request.form.get("code", "").strip()

        if code == "":
            flash("請輸入驗證碼", "danger")
            return redirect("/verify-code")

        db = get_db()

        reset_code = db.execute(
            """
            SELECT *
            FROM reset_codes
            WHERE email=?
            AND code=?
            AND is_used=0
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                session["reset_email"],
                code
            )
        ).fetchone()

        if reset_code is None:
            db.close()
            flash("驗證碼錯誤或已使用", "danger")
            return redirect("/verify-code")

        if get_reset_code_age_minutes(reset_code["created_at"]) > 10:
            db.close()
            flash("驗證碼已超過 10 分鐘，請重新取得", "danger")
            return redirect("/forgot-password")

        db.execute(
            """
            UPDATE reset_codes
            SET is_used=1
            WHERE id=?
            """,
            (reset_code["id"],)
        )

        db.commit()
        db.close()

        session["verified_reset"] = True

        flash("驗證成功，請設定新密碼", "success")
        return redirect("/reset-password")

    return render_template("verify_code.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():

    if not session.get("verified_reset") or "reset_email" not in session:
        flash("請先完成信箱驗證", "danger")
        return redirect("/forgot-password")

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password == "" or confirm_password == "":
            flash("請輸入新密碼與確認密碼", "danger")
            return redirect("/reset-password")

        if len(password) < 4:
            flash("密碼至少需要 4 個字元", "danger")
            return redirect("/reset-password")

        if password != confirm_password:
            flash("兩次輸入的密碼不一致", "danger")
            return redirect("/reset-password")

        db = get_db()

        db.execute(
            """
            UPDATE users
            SET password=?
            WHERE email=?
            """,
            (
                password,
                session["reset_email"]
            )
        )

        db.commit()
        db.close()

        session.pop("reset_email", None)
        session.pop("verified_reset", None)

        flash("密碼重設成功，請使用新密碼登入", "success")

        return redirect("/login")

    return render_template("reset_password.html")


@app.route("/coin-shop")
def coin_shop():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    coupons = db.execute(
        """
        SELECT *
        FROM coupons
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    db.close()

    return render_template(
        "coin_shop.html",
        coupons=coupons
    )


@app.route("/exchange-coupon")
def exchange_coupon():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    user = db.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    if user["coins"] < 1000:
        db.close()
        flash("金幣不足，需要 1000 金幣", "danger")
        return redirect("/coin-shop")

    new_coins = user["coins"] - 1000

    db.execute(
        """
        UPDATE users
        SET coins=?
        WHERE id=?
        """,
        (new_coins, session["user_id"])
    )

    db.execute(
        """
        INSERT INTO coupons(user_id, discount, is_used, created_at)
        VALUES (?, 100, 0, ?)
        """,
        (
            session["user_id"],
            now_text()
        )
    )

    add_coin_log(db, session["user_id"], -1000, "兌換 100 元折價券")

    db.commit()
    db.close()

    session["coins"] = new_coins

    flash("🎟️ 兌換成功！獲得 100 元折價券", "success")
    return redirect("/coin-shop")

@app.route("/daily-checkin")
def daily_checkin():

    if "user_id" not in session:
        return redirect("/login")

    today = datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d")

    db = get_db()

    try:
        db.execute(
            "ALTER TABLE users ADD COLUMN last_checkin TEXT"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

    user = db.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (session["user_id"],)
    ).fetchone()

    if user is None:
        db.close()
        session.clear()
        flash("登入狀態已失效，請重新登入", "danger")
        return redirect("/login")

    if user["last_checkin"] == today:
        db.close()
        flash("今天已經簽到過了！", "danger")
        return redirect("/")

    new_coins = user["coins"] + 100

    db.execute(
        """
        UPDATE users
        SET coins=?,
            last_checkin=?
        WHERE id=?
        """,
        (new_coins, today, session["user_id"])
    )

    add_coin_log(db, session["user_id"], 100, "每日簽到獎勵")

    db.commit()
    db.close()

    session["coins"] = new_coins

    flash("🎁 每日簽到成功！獲得 100 金幣", "success")
    return redirect("/")


@app.route("/add", methods=["GET", "POST"])
def add_product():
    if "user_id" not in session:
        return redirect("/login")

    if request.method == "POST":
        db = get_db()

        hours = int(request.form["hours"])
        end_time = datetime.now(TAIWAN_TIMEZONE) + timedelta(hours=hours)

        image_files = [
            request.files.get("image"),
            request.files.get("image1"),
            request.files.get("image2"),
            request.files.get("image3"),
            request.files.get("image4"),
            request.files.get("image5")
        ]

        image_paths = []

        for image_file in image_files:
            image_path = save_uploaded_image(image_file)
            if image_path:
                image_paths.append(image_path)

        if not image_paths:
            db.close()
            flash("請至少上傳一張商品圖片", "danger")
            return redirect("/add")

        while len(image_paths) < 5:
            image_paths.append(None)

        main_image = image_paths[0]
        condition = request.form.get("condition", "二手")
        trade_method = request.form.get("trade_method", "面交")
        meetup_time = request.form.get("meetup_time", "平日晚上")
        payment_method = request.form.get("payment_method", "現金")

        db.execute(
            """
            INSERT INTO products(
                title, start_price, current_price, image,
                image1, image2, image3, image4, image5,
                description, category, end_time, user_id,
                meet_location, condition,
                trade_method, meetup_time, payment_method
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form["title"],
                request.form["price"],
                request.form["price"],
                main_image,
                image_paths[0],
                image_paths[1],
                image_paths[2],
                image_paths[3],
                image_paths[4],
                request.form["description"],
                request.form["category"],
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                session["user_id"],
                request.form["meet_location"],
                condition,
                trade_method,
                meetup_time,
                payment_method
            )
        )

        product_count = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM products
            WHERE user_id=?
            """,
            (session["user_id"],)
        ).fetchone()["count"]

        # 首次上架獎勵保底欄位：避免舊資料庫沒有欄位導致無法發放
        try:
            db.execute(
                "ALTER TABLE users ADD COLUMN has_received_listing_bonus INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass

        listing_bonus_awarded = False

        user_bonus = db.execute(
            """
            SELECT
                IFNULL(coins, 0) AS coins,
                IFNULL(has_received_listing_bonus, 0) AS has_received_listing_bonus
            FROM users
            WHERE id=?
            """,
            (session["user_id"],)
        ).fetchone()

        if (
            user_bonus
            and int(user_bonus["has_received_listing_bonus"] or 0) == 0
        ):
            new_coins = int(user_bonus["coins"] or 0) + 1000

            db.execute(
                """
                UPDATE users
                SET coins=?,
                    has_received_listing_bonus=1
                WHERE id=?
                """,
                (
                    new_coins,
                    session["user_id"]
                )
            )

            session["coins"] = new_coins
            add_coin_log(db, session["user_id"], 1000, "首次上架獎勵")
            listing_bonus_awarded = True

        db.commit()
        db.close()

        if product_count >= 30:
            unlock_achievement(
                session["user_id"],
                "📦 上架達人",
                "上架商品達到 30 件"
            )

        check_all_achievements(session["user_id"])

        if listing_bonus_awarded:
            flash("🎉 商品新增成功！首次上架獎勵 1000 金幣已發放！", "success")
        else:
            flash("商品新增成功！", "success")

        return redirect("/")

    return render_template("add_product.html")


@app.route("/edit-product/<int:id>", methods=["GET", "POST"])
def edit_product(id):
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    product = db.execute(
        "SELECT * FROM products WHERE id=?",
        (id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    check_all_achievements(product["user_id"])

    if product["user_id"] != session["user_id"]:
        db.close()
        return "你不能編輯別人的商品"

    if request.method == "POST":
        title = request.form["title"]
        description = request.form["description"]
        category = request.form["category"]
        condition = request.form.get("condition", "二手")
        meet_location = request.form.get("meet_location", "")
        trade_method = request.form.get("trade_method", "面交")
        meetup_time = request.form.get("meetup_time", "平日晚上")
        payment_method = request.form.get("payment_method", "現金")
        hours = int(request.form["hours"])

        end_time = datetime.now(TAIWAN_TIMEZONE) + timedelta(hours=hours)
        image_path = product["image"]

        image = request.files.get("image")

        if image and image.filename != "":
            if product["image"]:
                old_image_path = product["image"].replace("/static/", "static/")

                if os.path.exists(old_image_path):
                    os.remove(old_image_path)

            ext = image.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"

            image.save(
                os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    filename
                )
            )

            image_path = "/static/uploads/" + filename

        db.execute(
            """
            UPDATE products
            SET title=?,
                image=?,
                description=?,
                category=?,
                condition=?,
                meet_location=?,
                trade_method=?,
                meetup_time=?,
                payment_method=?,
                end_time=?
            WHERE id=?
            """,
            (
                title,
                image_path,
                description,
                category,
                condition,
                meet_location,
                trade_method,
                meetup_time,
                payment_method,
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                id
            )
        )

        db.commit()
        db.close()

        flash("商品資料修改成功！", "success")
        return redirect("/my-products")

    db.close()

    return render_template(
        "edit_product.html",
        product=product
    )


@app.route("/product/<int:id>")
def product_detail(id):
    db = get_db()

    db.execute(
        """
        UPDATE products
        SET views = views + 1
        WHERE id=?
        """,
        (id,)
    )

    db.commit()

    product = db.execute(
        """
        SELECT products.*, users.username
        FROM products
        JOIN users
        ON products.user_id = users.id
        WHERE products.id=?
        """,
        (id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if "user_id" in session:
        db.execute(
            """
            INSERT INTO browsing_history(user_id, product_id, viewed_at)
            VALUES (?, ?, ?)
            """,
            (
                session["user_id"],
                id,
                datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        db.commit()

    seller_stats = db.execute(
        """
        SELECT
            IFNULL(AVG(rating), 0) AS avg_rating,
            COUNT(*) AS review_count
        FROM reviews
        WHERE target_user_id=?
        """,
        (product["user_id"],)
    ).fetchone()

    bids = db.execute(
        """
        SELECT bids.*, users.username
        FROM bids
        JOIN users
        ON bids.user_id = users.id
        WHERE product_id=?
        ORDER BY bid_price DESC
        """,
        (id,)
    ).fetchall()

    messages = db.execute(
        """
        SELECT
            chat_messages.*,
            users.username,
            IFNULL(AVG(reviews.rating), 0) AS avg_rating,
            COUNT(DISTINCT reviews.id) AS review_count,
            SUM(CASE WHEN chat_reactions.reaction='👍' THEN 1 ELSE 0 END) AS like_count,
            SUM(CASE WHEN chat_reactions.reaction='😂' THEN 1 ELSE 0 END) AS laugh_count,
            SUM(CASE WHEN chat_reactions.reaction='🔥' THEN 1 ELSE 0 END) AS fire_count
        FROM chat_messages
        JOIN users
        ON chat_messages.user_id = users.id
        LEFT JOIN reviews
        ON reviews.target_user_id = users.id
        LEFT JOIN chat_reactions
        ON chat_reactions.message_id = chat_messages.id
        WHERE chat_messages.product_id=?
        GROUP BY chat_messages.id
        ORDER BY chat_messages.id ASC
        """,
        (id,)
    ).fetchall()

    reviews = db.execute(
        """
        SELECT
            reviews.*,
            reviewer.username AS reviewer_name,
            target.username AS target_name
        FROM reviews
        JOIN users AS reviewer
        ON reviews.reviewer_id = reviewer.id
        JOIN users AS target
        ON reviews.target_user_id = target.id
        WHERE reviews.product_id=?
        ORDER BY reviews.id DESC
        """,
        (id,)
    ).fetchall()

    coupons = []

    if "user_id" in session:
        coupons = db.execute(
            """
            SELECT *
            FROM coupons
            WHERE user_id=?
            AND is_used=0
            ORDER BY id DESC
            """,
            (session["user_id"],)
        ).fetchall()

    winner_message = None
    winner = None

    try:
        end_time = datetime.strptime(
            product["end_time"],
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        end_time = datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)

    if datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None) > end_time:
        winner = db.execute(
            """
            SELECT bids.*, users.username
            FROM bids
            JOIN users
            ON bids.user_id = users.id
            WHERE bids.product_id=?
            ORDER BY bids.bid_price DESC
            LIMIT 1
            """,
            (id,)
        ).fetchone()

        if winner:
            unlock_achievement(
                winner["user_id"],
                "🏆 首次成交",
                "完成第一筆成功交易"
            )

            unlock_achievement(
                product["user_id"],
                "🏆 首次成交",
                "完成第一筆成功交易"
            )

        if winner and session.get("user_id") == winner["user_id"]:
            winner_message = "🎉 恭喜你已得標！請盡快與賣家聯絡。"

    current_price_safe = int(product["current_price"] or 0)
    bid_increment = get_bid_increment(current_price_safe)
    min_bid_price = current_price_safe + bid_increment
    product_images = get_product_images(product)

    # 商品頁被打開時也順手檢查一次倒數提醒與交易訂單
    notify_ending_soon_products()
    trade_order = create_trade_order_if_needed(id)

    questions = db.execute(
        """
        SELECT
            product_questions.*,
            users.username AS asker_name
        FROM product_questions
        JOIN users
        ON product_questions.asker_id = users.id
        WHERE product_questions.product_id=?
        ORDER BY product_questions.id DESC
        """,
        (id,)
    ).fetchall()

    if trade_order is None:
        trade_order = db.execute(
            "SELECT * FROM trade_orders WHERE product_id=?",
            (id,)
        ).fetchone()

    db.close()

    return render_template(
        "product_detail.html",
        product=product,
        product_images=product_images,
        bid_increment=bid_increment,
        min_bid_price=min_bid_price,
        bids=bids,
        messages=messages,
        winner_message=winner_message,
        winner=winner,
        reviews=reviews,
        seller_stats=seller_stats,
        coupons=coupons,
        questions=questions,
        trade_order=trade_order,
        is_ended=is_product_ended(product)
    )


@app.route("/bid/<int:id>", methods=["POST"])
def bid(id):
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    product = db.execute(
        "SELECT * FROM products WHERE id=?",
        (id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"


    if is_product_ended(product):
        db.close()
        create_trade_order_if_needed(id)
        flash("⛔ 此商品已結標，不能再出價", "danger")
        return redirect(f"/product/{id}")

    if "is_completed" in product.keys() and product["is_completed"] == 1:
        db.close()
        flash("此商品已完成交易，不能再競標", "danger")
        return redirect(f"/product/{id}")

    if "is_paused" in product.keys() and product["is_paused"] == 1:
        db.close()
        flash("此商品目前已暫停販售，不能競標", "danger")
        return redirect(f"/product/{id}")

    blocked = db.execute(
        """
        SELECT * FROM blacklist
        WHERE product_id=? AND blocked_user_id=?
        """,
        (id, session["user_id"])
    ).fetchone()

    if blocked:
        db.close()
        flash("你已被賣家封鎖，無法競標此商品。", "danger")
        return redirect(f"/product/{id}")

    if product["user_id"] == session["user_id"]:
        db.close()
        flash("賣家不能競標自己的商品", "danger")
        return redirect(f"/product/{id}")

    try:
        bid_price = int(request.form["bid_price"])
    except Exception:
        db.close()
        flash("出價金額格式錯誤", "danger")
        return redirect(f"/product/{id}")

    current_price_safe = int(product["current_price"] or 0)
    bid_increment = get_bid_increment(current_price_safe)
    min_bid_price = current_price_safe + bid_increment

    if bid_price < min_bid_price:
        db.close()
        flash(f"最低出價需為 NT$ {min_bid_price}，目前價格需至少加 {bid_increment} 元", "danger")
        return redirect(f"/product/{id}")

    old_winner = db.execute(
        """
        SELECT * FROM bids
        WHERE product_id=?
        ORDER BY bid_price DESC
        LIMIT 1
        """,
        (id,)
    ).fetchone()

    db.execute(
        """
        INSERT INTO bids(product_id, user_id, bid_price, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            id,
            session["user_id"],
            bid_price,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    db.execute(
        """
        UPDATE products
        SET current_price=?
        WHERE id=?
        """,
        (bid_price, id)
    )

    bid_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM bids
        WHERE user_id=?
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    is_lightning_bid = False

    try:
        end_time = datetime.strptime(
            product["end_time"],
            "%Y-%m-%d %H:%M:%S"
        )

        seconds_left = (
            end_time
            - datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)
        ).total_seconds()

        if seconds_left >= 0 and seconds_left <= 60:
            is_lightning_bid = True

    except Exception:
        is_lightning_bid = False

    db.commit()
    db.close()

    unlock_achievement(
        session["user_id"],
        "🏆 第一次下標",
        "完成第一次商品競標"
    )

    if bid_count >= 50:
        unlock_achievement(
            session["user_id"],
            "🔥 競標達人",
            "累積競標達到 50 次"
        )

    if bid_price >= 10000:
        unlock_achievement(
            session["user_id"],
            "💰 出價王",
            "最高出價超過 10000"
        )

    if is_lightning_bid:
        unlock_achievement(
            session["user_id"],
            "⚡ 閃電競標",
            "在結標前 1 分鐘內完成下標"
        )

    check_all_achievements(session["user_id"])


    if old_winner and old_winner["user_id"] != session["user_id"]:
        create_notification_realtime(
            old_winner["user_id"],
            f"你競標的商品已被超價：{product['title']}",
            f"/product/{id}"
        )

    fav_db = get_db()
    favorite_users = fav_db.execute(
        """
        SELECT user_id
        FROM favorites
        WHERE product_id=?
        AND user_id != ?
        """,
        (id, session["user_id"])
    ).fetchall()
    fav_db.close()

    for fav_user in favorite_users:
        create_notification_realtime(
            fav_user["user_id"],
            f"你收藏的商品有新出價：{product['title']}",
            f"/product/{id}"
        )

    socketio.emit(
        "new_bid",
        {
            "product_id": id,
            "price": bid_price,
            "username": session.get("username", "匿名競標者"),
            "is_auto_bid": False
        },
        room=str(id)
    )

    emit_npc_message(
        id,
        current_price=bid_price,
        end_time_text=product["end_time"],
        chance=0.35
    )

    flash("出價成功！", "success")
    return redirect(f"/product/{id}")


@app.route("/use-coupon/<int:product_id>", methods=["POST"])
def use_coupon(product_id):

    if "user_id" not in session:
        return redirect("/login")

    coupon_id = request.form["coupon_id"]

    db = get_db()

    product = db.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    end_time = datetime.strptime(
        product["end_time"],
        "%Y-%m-%d %H:%M:%S"
    )

    if datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None) <= end_time:
        db.close()
        flash("競標尚未結束，不能使用折價券", "danger")
        return redirect(f"/product/{product_id}")

    winner = db.execute(
        """
        SELECT *
        FROM bids
        WHERE product_id=?
        ORDER BY bid_price DESC
        LIMIT 1
        """,
        (product_id,)
    ).fetchone()

    if winner is None:
        db.close()
        flash("目前沒有得標者", "danger")
        return redirect(f"/product/{product_id}")

    if winner["user_id"] != session["user_id"]:
        db.close()
        flash("只有得標者可以使用折價券", "danger")
        return redirect(f"/product/{product_id}")

    coupon = db.execute(
        """
        SELECT *
        FROM coupons
        WHERE id=?
        AND user_id=?
        AND is_used=0
        """,
        (
            coupon_id,
            session["user_id"]
        )
    ).fetchone()

    if coupon is None:
        db.close()
        flash("找不到可用折價券", "danger")
        return redirect(f"/product/{product_id}")

    final_price = winner["bid_price"] - coupon["discount"]

    if final_price < 0:
        final_price = 0

    db.execute(
        """
        UPDATE bids
        SET bid_price=?
        WHERE id=?
        """,
        (
            final_price,
            winner["id"]
        )
    )

    db.execute(
        """
        UPDATE products
        SET current_price=?
        WHERE id=?
        """,
        (
            final_price,
            product_id
        )
    )

    db.execute(
        """
        UPDATE coupons
        SET is_used=1
        WHERE id=?
        """,
        (coupon_id,)
    )

    db.commit()
    db.close()

    socketio.emit(
        "new_bid",
        {
            "product_id": product_id,
            "price": final_price
        },
        room=str(product_id)
    )

    flash(
        f"🎟️ 已使用 {coupon['discount']} 元折價券，實際得標金額為 NT$ {final_price}",
        "success"
    )

    return redirect(f"/product/{product_id}")


@app.route("/notifications")
def notifications():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    notes = db.execute(
        """
        SELECT * FROM notifications
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    db.execute(
        """
        UPDATE notifications
        SET is_read=1
        WHERE user_id=?
        """,
        (session["user_id"],)
    )

    db.commit()
    db.close()

    return render_template(
        "notifications.html",
        notifications=notes
    )


@app.route("/toggle-product-status/<int:id>", methods=["POST"])
def toggle_product_status(id):
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    product = db.execute(
        "SELECT * FROM products WHERE id=?",
        (id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if product["user_id"] != session["user_id"]:
        db.close()
        flash("你不能修改別人的商品狀態", "danger")
        return redirect("/my-products")

    current_status = product["is_paused"] if "is_paused" in product.keys() else 0
    new_status = 0 if current_status == 1 else 1

    db.execute(
        """
        UPDATE products
        SET is_paused=?
        WHERE id=?
        """,
        (new_status, id)
    )

    db.commit()
    db.close()

    if new_status == 1:
        flash("商品已暫停販售，首頁與直播不會顯示，也不能被競標", "success")
    else:
        flash("商品已恢復販售", "success")

    return redirect("/my-products")


@app.route("/delete-product/<int:id>")
def delete_product(id):
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    product = db.execute(
        "SELECT * FROM products WHERE id=?",
        (id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if product["user_id"] != session["user_id"]:
        db.close()
        return "你不能刪除別人的商品"

    if product["image"]:
        image_path = product["image"].replace("/static/", "static/")

        if os.path.exists(image_path):
            os.remove(image_path)

    db.execute("DELETE FROM bids WHERE product_id=?", (id,))
    db.execute("DELETE FROM chat_messages WHERE product_id=?", (id,))
    db.execute("DELETE FROM products WHERE id=?", (id,))

    db.commit()
    db.close()

    flash("商品已刪除", "success")
    return redirect("/my-products")

@app.route("/profile/edit", methods=["GET", "POST"])
def edit_profile():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    user = db.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    if user is None:
        db.close()
        session.clear()
        flash("登入狀態已失效，請重新登入", "danger")
        return redirect("/login")

    if request.method == "POST":
        avatar_path = user["avatar"] if "avatar" in user.keys() and user["avatar"] else None
        banner_path = user["banner"] if "banner" in user.keys() and user["banner"] else None

        new_avatar = save_uploaded_image(request.files.get("avatar"))
        new_banner = save_uploaded_image(request.files.get("banner"))

        if new_avatar:
            avatar_path = new_avatar

        if new_banner:
            banner_path = new_banner

        db.execute(
            """
            UPDATE users
            SET avatar=?,
                banner=?
            WHERE id=?
            """,
            (
                avatar_path,
                banner_path,
                session["user_id"]
            )
        )

        db.commit()
        db.close()

        # 重點修正：更新資料庫後，立刻同步 session
        if avatar_path:
            session["avatar"] = avatar_path
        else:
            session["avatar"] = "/static/default-avatar.png"

        if banner_path:
            session["banner"] = banner_path
        else:
            session["banner"] = "/static/default-banner.png"

        flash("個人頭像與封面已更新", "success")
        return redirect(f"/profile/{session['user_id']}")

    db.close()

    return render_template(
        "edit_profile.html",
        user=user
    )


@app.route("/profile/<int:user_id>")
def profile(user_id):

    db = get_db()

    user = db.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (user_id,)
    ).fetchone()

    if user is None:
        db.close()
        return "找不到使用者"

    # 商品數

    product_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM products
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()["count"]

    # 評價

    rating_data = db.execute(
        """
        SELECT
            AVG(rating) AS avg_rating,
            COUNT(*) AS review_count
        FROM reviews
        WHERE target_user_id=?
        """,
        (user_id,)
    ).fetchone()

    # 成交數

    sold_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM products
        WHERE user_id=?
        AND current_price > start_price
        """,
        (user_id,)
    ).fetchone()["count"]

    bid_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM bids
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()["count"]

    user_level = get_user_level(
        product_count=product_count,
        sold_count=sold_count,
        bid_count=bid_count
    )

    # 信任統計資料：profile.html 會使用 trust_stats，這裡一定要傳入完整欄位，避免 Jinja UndefinedError
    avg_rating = round(float(rating_data["avg_rating"] or 0), 1)
    review_count = int(rating_data["review_count"] or 0)

    trust_stats = {
        "email_verified": (
            "email" in user.keys()
            and user["email"]
            and user["email"].endswith("@gm.student.ncut.edu.tw")
        ),
        "completed_sales": int(sold_count or 0),
        "completed_trades": int(sold_count or 0),
        "avg_rating": avg_rating,
        "review_score": avg_rating,
        "review_count": review_count,
        "reply_speed": "普通",
        "meetup_success_rate": 100 if int(sold_count or 0) > 0 else 0,
        "identity_badge": "已驗證" if (
            "email" in user.keys()
            and user["email"]
            and user["email"].endswith("@gm.student.ncut.edu.tw")
        ) else "一般會員"
    }

    # 商品

    products = db.execute(
        """
        SELECT *
        FROM products
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (user_id,)
    ).fetchall()

    check_all_achievements(user_id)

    achievements = db.execute(
        """
        SELECT *
        FROM achievements
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (user_id,)
    ).fetchall()

    db.close()

    return render_template(
        "profile.html",
        user=user,
        product_count=product_count,
        rating_data=rating_data,
        sold_count=sold_count,
        user_level=user_level,
        trust_stats=trust_stats,
        products=products,
        achievements=achievements
    )


@app.route("/my-products")
def my_products():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    products = db.execute(
        "SELECT * FROM products WHERE user_id=?",
        (session["user_id"],)
    ).fetchall()

    db.close()

    return render_template(
        "my_products.html",
        products=products
    )


@app.route("/my-bids")
def my_bids():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    bids = db.execute(
        """
        SELECT
            bids.*,
            products.title,
            products.image,
            products.current_price,

            CASE
                WHEN datetime(products.end_time) > datetime('now', '+8 hours')
                THEN 2

                WHEN bids.bid_price = products.current_price
                THEN 1

                ELSE 0
            END AS is_winner

        FROM bids

        JOIN products
        ON bids.product_id = products.id

        WHERE bids.user_id=?

        ORDER BY bids.id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    db.close()

    return render_template(
        "my_bids.html",
        bids=bids
    )


@app.route("/edit-message/<int:message_id>", methods=["POST"])
def edit_message(message_id):
    if "user_id" not in session:
        return redirect("/login")

    new_message = request.form["message"].strip()

    if new_message == "":
        flash("留言不能是空白", "danger")
        return redirect(request.referrer)

    db = get_db()

    message = db.execute(
        "SELECT * FROM chat_messages WHERE id=?",
        (message_id,)
    ).fetchone()

    if message is None:
        db.close()
        return "找不到留言"

    if message["user_id"] != session["user_id"]:
        db.close()
        return "你不能編輯別人的留言"

    db.execute(
        """
        UPDATE chat_messages
        SET message=?
        WHERE id=?
        """,
        (new_message, message_id)
    )

    db.commit()
    db.close()

    flash("留言修改成功！", "success")
    return redirect(request.referrer)


@app.route("/delete-message/<int:message_id>", methods=["POST"])
def delete_message(message_id):
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    message = db.execute(
        "SELECT * FROM chat_messages WHERE id=?",
        (message_id,)
    ).fetchone()

    if message is None:
        db.close()
        return "找不到留言"

    if message["user_id"] != session["user_id"]:
        db.close()
        return "你不能收回別人的留言"

    db.execute(
        """
        UPDATE chat_messages
        SET message='此訊息已收回'
        WHERE id=?
        """,
        (message_id,)
    )

    db.commit()
    db.close()

    flash("訊息已收回", "success")
    return redirect(request.referrer)


@app.route("/block-user/<int:product_id>/<int:user_id>", methods=["POST"])
def block_user(product_id, user_id):
    if "user_id" not in session:
        return redirect("/login")

    reason = request.form.get("reason", "騷擾或垃圾訊息")

    db = get_db()

    product = db.execute(
        "SELECT * FROM products WHERE id=?",
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if product["user_id"] != session["user_id"]:
        db.close()
        return "只有賣家可以封鎖此商品聊天室的使用者"

    existing = db.execute(
        """
        SELECT * FROM blacklist
        WHERE product_id=? AND blocked_user_id=?
        """,
        (product_id, user_id)
    ).fetchone()

    if existing:
        db.close()
        flash("此使用者已在黑名單中", "danger")
        return redirect(f"/product/{product_id}")

    db.execute(
        """
        INSERT INTO blacklist(
            product_id,
            blocked_user_id,
            blocked_by,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            product_id,
            user_id,
            session["user_id"],
            reason,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    db.commit()
    db.close()

    flash("已將使用者加入黑名單", "success")
    return redirect(f"/product/{product_id}")



@app.route("/complete-transaction/<int:product_id>", methods=["POST"])
def complete_transaction(product_id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN is_completed INTEGER DEFAULT 0"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

    product = db.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if product["user_id"] != session["user_id"]:
        db.close()
        flash("只有賣家可以完成交易", "danger")
        return redirect(f"/product/{product_id}")

    if product["is_completed"] == 1:
        db.close()
        flash("此商品已經完成交易", "danger")
        return redirect(f"/product/{product_id}")

    end_time = datetime.strptime(
        product["end_time"],
        "%Y-%m-%d %H:%M:%S"
    )

    if datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None) <= end_time:
        db.close()
        flash("競標尚未結束，不能完成交易", "danger")
        return redirect(f"/product/{product_id}")

    winner = db.execute(
        """
        SELECT *
        FROM bids
        WHERE product_id=?
        ORDER BY bid_price DESC
        LIMIT 1
        """,
        (product_id,)
    ).fetchone()

    if winner is None:
        db.close()
        flash("目前沒有得標者，不能完成交易", "danger")
        return redirect(f"/product/{product_id}")

    db.execute(
        """
        UPDATE products
        SET is_completed=1,
            is_pinned=0,
            pin_expire_time=NULL
        WHERE id=?
        """,
        (product_id,)
    )

    db.commit()
    db.close()

    unlock_achievement(
        session["user_id"],
        "🏆 首次成交",
        "完成第一筆成功交易"
    )

    unlock_achievement(
        winner["user_id"],
        "🏆 首次成交",
        "完成第一筆成功交易"
    )

    create_notification(
        winner["user_id"],
        f"你得標的商品已完成交易：{product['title']}",
        f"/product/{product_id}"
    )

    flash("✅ 交易已完成，商品已自動從首頁下架，現在可進行互評", "success")

    return redirect(f"/product/{product_id}")


@app.route("/review/<int:product_id>", methods=["POST"])
def review(product_id):
    if "user_id" not in session:
        return redirect("/login")

    rating = int(request.form["rating"])
    comment = request.form["comment"].strip()
    target_user_id = int(request.form["target_user_id"])

    if rating < 1 or rating > 5:
        flash("評分必須是 1 到 5 顆星", "danger")
        return redirect(f"/product/{product_id}")

    if comment == "":
        flash("評價內容不能空白", "danger")
        return redirect(f"/product/{product_id}")

    db = get_db()

    product = db.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if "is_completed" not in product.keys() or product["is_completed"] != 1:
        db.close()
        flash("交易完成後才可以評價", "danger")
        return redirect(f"/product/{product_id}")

    winner = db.execute(
        """
        SELECT *
        FROM bids
        WHERE product_id=?
        ORDER BY bid_price DESC
        LIMIT 1
        """,
        (product_id,)
    ).fetchone()

    if winner is None:
        db.close()
        flash("沒有得標者，不能評價", "danger")
        return redirect(f"/product/{product_id}")

    seller_id = product["user_id"]
    buyer_id = winner["user_id"]

    valid_buyer_review = (
        session["user_id"] == buyer_id
        and target_user_id == seller_id
    )

    valid_seller_review = (
        session["user_id"] == seller_id
        and target_user_id == buyer_id
    )

    if not valid_buyer_review and not valid_seller_review:
        db.close()
        flash("只有賣家與得標者可以互相評價", "danger")
        return redirect(f"/product/{product_id}")

    existing_review = db.execute(
        """
        SELECT * FROM reviews
        WHERE product_id=?
        AND reviewer_id=?
        AND target_user_id=?
        """,
        (
            product_id,
            session["user_id"],
            target_user_id
        )
    ).fetchone()

    if existing_review:
        db.close()
        flash("你已經評價過了", "danger")
        return redirect(f"/product/{product_id}")

    db.execute(
        """
        INSERT INTO reviews(
            product_id,
            reviewer_id,
            target_user_id,
            rating,
            comment,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            session["user_id"],
            target_user_id,
            rating,
            comment,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    review_stats = db.execute(
        """
        SELECT
            IFNULL(AVG(rating), 0) AS avg_rating,
            COUNT(*) AS review_count
        FROM reviews
        WHERE target_user_id=?
        """,
        (target_user_id,)
    ).fetchone()

    db.commit()
    db.close()

    if (
        review_stats["review_count"] >= 1
        and review_stats["avg_rating"] >= 5
    ):
        unlock_achievement(
            target_user_id,
            "⭐ 五星賣家",
            "賣家評價達到 5.0 顆星"
        )

    check_all_achievements(target_user_id)

    create_notification(
        target_user_id,
        "你收到一則新的交易評價",
        f"/product/{product_id}"
    )

    flash("評價送出成功！", "success")
    return redirect(f"/product/{product_id}")


@socketio.on("join_user_notifications")
def handle_join_user_notifications():

    if "user_id" not in session:
        return

    join_room(f"user_{session['user_id']}")


@socketio.on("join_product")
def handle_join(data):
    product_id = str(data["product_id"])

    join_room(product_id)

    if "user_id" in session:
        join_room(f"user_{session['user_id']}")

    if product_id not in online_users:
        online_users[product_id] = 0

    online_users[product_id] += 1

    emit(
        "online_count",
        {
            "count": online_users[product_id]
        },
        room=product_id
    )


@socketio.on("leave_product")
def handle_leave(data):
    product_id = str(data["product_id"])

    if product_id in online_users:
        online_users[product_id] -= 1

        if online_users[product_id] < 0:
            online_users[product_id] = 0

        emit(
            "online_count",
            {
                "count": online_users[product_id]
            },
            room=product_id
        )
@socketio.on("send_message")
def handle_send_message(data):

    if "user_id" not in session:
        return

    product_id = str(data["product_id"])
    message = data["message"].strip()

    if message == "":
        return

    db = get_db()

    # 直播聊天室

    if product_id == "live_room":

        emit(
            "new_message",
            {
                "username": session["username"],
                "message": message,
                "time": datetime.now(TAIWAN_TIMEZONE).strftime("%H:%M:%S")
            },
            room="live_room"
        )

        db.close()
        return

    # 商品聊天室

    product = db.execute(
        """
        SELECT * FROM products
        WHERE id=?
        """,
        (product_id,)
    ).fetchone()

    blocked = db.execute(
        """
        SELECT * FROM blacklist
        WHERE product_id=?
        AND blocked_user_id=?
        """,
        (
            product_id,
            session["user_id"]
        )
    ).fetchone()

    if blocked:
        db.close()

        emit(
            "new_message",
            {
                "username": "系統",
                "message": "你已被封鎖，無法在此商品聊天室留言。",
                "time": datetime.now(TAIWAN_TIMEZONE).strftime("%H:%M:%S")
            },
            room=str(product_id)
        )

        return

    created_time = datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

    cursor = db.execute(
        """
        INSERT INTO chat_messages(
            product_id,
            user_id,
            message,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            product_id,
            session["user_id"],
            message,
            created_time
        )
    )

    message_id = cursor.lastrowid

    db.commit()

    sender_id = session["user_id"]

    buyers = db.execute(
        """
        SELECT DISTINCT user_id
        FROM bids
        WHERE product_id=?
        """,
        (product_id,)
    ).fetchall()

    # 買家留言 → 賣家通知

    if product and sender_id != product["user_id"]:

        create_notification_realtime(
            product["user_id"],
            f"你的商品有買家留言：{product['title']}",
            f"/product/{product_id}"
        )

    # 賣家留言 → 買家通知

    if product and sender_id == product["user_id"]:

        for buyer in buyers:

            buyer_id = buyer["user_id"]

            if buyer_id != sender_id:

                create_notification_realtime(
                    buyer_id,
                    f"賣家在商品聊天室留言：{product['title']}",
                    f"/product/{product_id}"
                )

    # 競標中留言 → 其他出價者通知

    if product:

        end_time = datetime.strptime(
            product["end_time"],
            "%Y-%m-%d %H:%M:%S"
        )

        is_bidding = (
            datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)
            < end_time
        )

        if is_bidding:

            for buyer in buyers:

                buyer_id = buyer["user_id"]

                if (
                    buyer_id != sender_id
                    and buyer_id != product["user_id"]
                ):

                    create_notification_realtime(
                        buyer_id,
                        f"你參與競標的商品有新留言：{product['title']}",
                        f"/product/{product_id}"
                    )

    # 賣家 TAG 通知

    if product and sender_id == product["user_id"]:

        tagged_users = db.execute(
            """
            SELECT id, username
            FROM users
            """
        ).fetchall()

        for u in tagged_users:

            tag_text = "@" + u["username"]

            if (
                tag_text in message
                and u["id"] != sender_id
            ):

                create_notification_realtime(
                    u["id"],
                    f"賣家回覆了你：{product['title']}",
                    f"/product/{product_id}"
                )

    # 全站聊天室通知

    users = db.execute(
        """
        SELECT id
        FROM users
        WHERE id != ?
        """,
        (sender_id,)
    ).fetchall()

    if product:
        for user in users:

            create_notification_realtime(
                user["id"],
                f"商品聊天室有新留言：{product['title']}",
                f"/product/{product_id}"
            )

    db.close()

    emit(
        "new_message",
        {
            "message_id": message_id,
            "username": session["username"],
            "message": message,
            "time": datetime.now(TAIWAN_TIMEZONE).strftime("%H:%M:%S")
        },
        room=str(product_id)
    )



ADMIN_ROLES = {
    "super_admin": "超級管理員",
    "support_admin": "客服管理員",
    "review_admin": "審核管理員",
}


def current_admin_role():
    role = session.get("admin_role", "user")

    # 舊版資料只有 is_admin=1 時，視為超級管理員，避免原管理員被鎖在外面。
    if role == "user" and session.get("is_admin") == 1:
        return "super_admin"

    return role


def is_admin():
    return current_admin_role() in ADMIN_ROLES


def is_super_admin():
    return current_admin_role() == "super_admin"


def require_admin_role(*roles):
    """超級管理員全通；其他角色只允許指定功能。"""

    if not is_admin():
        return False

    role = current_admin_role()

    if role == "super_admin":
        return True

    return role in roles


def admin_role_name(role=None):
    return ADMIN_ROLES.get(role or current_admin_role(), "一般會員")


def is_admin_panel_verified():
    """管理員已登入，且已通過後台二次密碼驗證。"""
    return is_admin() and session.get("admin_panel_verified") is True


@app.before_request
def protect_admin_routes():
    """所有 /admin 開頭的後台功能，都需要先通過後台密碼驗證。"""

    if not request.path.startswith("/admin"):
        return None

    # 後台密碼頁與後台驗證登出不需要再次驗證，避免無限轉址。
    if request.path in ["/admin-login", "/admin-logout"]:
        return None

    if not is_admin():
        return "你不是管理員，無法進入後台"

    if not session.get("admin_panel_verified"):
        if request.method == "GET":
            session["admin_next"] = request.full_path if request.query_string else request.path
        else:
            session["admin_next"] = "/admin"

        flash("請先輸入管理員後台密碼", "warning")
        return redirect("/admin-login")

    return None


@app.context_processor
def inject_admin_role_helpers():
    return dict(
        current_admin_role=current_admin_role(),
        admin_role_name=admin_role_name,
        require_admin_role=require_admin_role,
        is_super_admin=is_super_admin()
    )


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if not is_admin():
        return "你不是管理員，無法進入後台"

    if request.method == "POST":
        password = request.form.get("admin_password", "")

        if password == ADMIN_PANEL_PASSWORD:
            session["admin_panel_verified"] = True
            next_url = session.pop("admin_next", "/admin")
            flash("後台驗證成功", "success")
            return redirect(next_url or "/admin")

        flash("後台密碼錯誤", "danger")
        return redirect("/admin-login")

    return render_template("admin_login.html")


@app.route("/admin-logout")
def admin_logout():
    session.pop("admin_panel_verified", None)
    session.pop("admin_next", None)
    flash("已退出管理員後台驗證", "success")
    return redirect("/")


@app.route("/chat-reaction-safe/<int:message_id>", methods=["POST"])
def chat_reaction_safe(message_id):
    reaction = request.form.get("reaction")

    if not reaction and request.is_json:
        data = request.get_json(silent=True) or {}
        reaction = data.get("reaction")

    return handle_chat_reaction(message_id, reaction)


@app.route("/chat-reaction/<int:message_id>/<path:reaction>", methods=["POST"])
def chat_reaction(message_id, reaction):
    return handle_chat_reaction(message_id, reaction)


def handle_chat_reaction(message_id, reaction):
    if "user_id" not in session:
        return {"success": False, "message": "請先登入"}, 401

    allowed_reactions = ["👍", "😂", "🔥"]

    if reaction not in allowed_reactions:
        return {"success": False, "message": "不支援的反應"}, 400

    db = get_db()

    message = db.execute(
        """
        SELECT *
        FROM chat_messages
        WHERE id=?
        """,
        (message_id,)
    ).fetchone()

    if message is None:
        db.close()
        return {"success": False, "message": "找不到留言"}, 404

    existing = db.execute(
        """
        SELECT *
        FROM chat_reactions
        WHERE message_id=?
        AND user_id=?
        AND reaction=?
        """,
        (
            message_id,
            session["user_id"],
            reaction
        )
    ).fetchone()

    if existing:
        db.execute(
            """
            DELETE FROM chat_reactions
            WHERE id=?
            """,
            (existing["id"],)
        )
    else:
        db.execute(
            """
            INSERT INTO chat_reactions(
                message_id,
                user_id,
                reaction,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                message_id,
                session["user_id"],
                reaction,
                datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            )
        )

    product_id = message["product_id"]

    counts = db.execute(
        """
        SELECT reaction, COUNT(*) AS count
        FROM chat_reactions
        WHERE message_id=?
        GROUP BY reaction
        """,
        (message_id,)
    ).fetchall()

    result = {
        "👍": 0,
        "😂": 0,
        "🔥": 0
    }

    for c in counts:
        result[c["reaction"]] = c["count"]

    db.commit()
    db.close()

    socketio.emit(
        "reaction_update",
        {
            "message_id": message_id,
            "counts": result
        },
        room=str(product_id)
    )

    return {
        "success": True,
        "counts": result
    }


@app.route("/report-product/<int:product_id>", methods=["POST"], endpoint="report_product_v2")
def report_product_v2(product_id):
    if "user_id" not in session:
        return redirect("/login")

    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("請填寫檢舉原因", "danger")
        return redirect(f"/product/{product_id}")

    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if product is None:
        db.close()
        return "找不到商品"

    db.execute(
        """
        INSERT INTO reports(product_id, reporter_id, reason, status, created_at)
        VALUES (?, ?, ?, '待審核', ?)
        """,
        (
            product_id,
            session["user_id"],
            reason,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    db.commit()
    db.close()

    flash("檢舉已送出，管理員會進行審核。", "success")
    return redirect(f"/product/{product_id}")


@app.route("/question/<int:product_id>", methods=["POST"])
def ask_product_question(product_id):
    if "user_id" not in session:
        return redirect("/login")

    question = request.form.get("question", "").strip()
    if not question:
        flash("請輸入問題內容", "danger")
        return redirect(f"/product/{product_id}")

    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if product is None:
        db.close()
        return "找不到商品"

    db.execute(
        """
        INSERT INTO product_questions(product_id, asker_id, question, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            product_id,
            session["user_id"],
            question,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    db.commit()
    db.close()

    if product["user_id"] != session["user_id"]:
        create_notification_realtime(
            product["user_id"],
            f"💬 有人詢問你的商品：{product['title']}",
            f"/product/{product_id}"
        )

    flash("問題已送出", "success")
    return redirect(f"/product/{product_id}")


@app.route("/answer-question/<int:question_id>", methods=["POST"])
def answer_product_question(question_id):
    if "user_id" not in session:
        return redirect("/login")

    answer = request.form.get("answer", "").strip()
    db = get_db()
    question = db.execute(
        """
        SELECT product_questions.*, products.user_id AS seller_id, products.title
        FROM product_questions
        JOIN products ON product_questions.product_id = products.id
        WHERE product_questions.id=?
        """,
        (question_id,)
    ).fetchone()

    if question is None:
        db.close()
        return "找不到問題"

    if question["seller_id"] != session["user_id"] and not is_admin():
        db.close()
        return "你沒有權限回覆此問題"

    db.execute(
        """
        UPDATE product_questions
        SET answer=?, answered_at=?
        WHERE id=?
        """,
        (answer, datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"), question_id)
    )
    db.commit()
    db.close()

    create_notification_realtime(
        question["asker_id"],
        f"💬 賣家已回覆你的商品問題：{question['title']}",
        f"/product/{question['product_id']}"
    )

    flash("已回覆問題", "success")
    return redirect(f"/product/{question['product_id']}")


@app.route("/trade-order/<int:order_id>/pay", methods=["GET", "POST"])
def pay_trade_order(order_id):
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()
    order = db.execute(
        """
        SELECT trade_orders.*, products.title, products.image, products.meet_location,
               seller.username AS seller_name, buyer.username AS buyer_name
        FROM trade_orders
        LEFT JOIN products ON trade_orders.product_id = products.id
        LEFT JOIN users AS seller ON trade_orders.seller_id = seller.id
        LEFT JOIN users AS buyer ON trade_orders.buyer_id = buyer.id
        WHERE trade_orders.id=?
        """,
        (order_id,)
    ).fetchone()

    if order is None:
        db.close()
        return "找不到交易訂單"

    if session["user_id"] != order["buyer_id"] and not is_admin():
        db.close()
        return "只有得標買家可以付款"

    if request.method == "POST":
        payment_method = request.form.get("payment_method", "").strip()
        payment_note = request.form.get("payment_note", "").strip()

        allowed_methods = ["校園錢包", "面交現金", "銀行轉帳", "LINE Pay 模擬"]
        if payment_method not in allowed_methods:
            db.close()
            flash("付款方式不正確", "danger")
            return redirect(f"/trade-order/{order_id}/pay")

        if order["payment_status"] == "已付款":
            db.close()
            flash("此訂單已付款，請勿重複付款", "warning")
            return redirect(f"/product/{order['product_id']}")

        if payment_method == "校園錢包":
            buyer = db.execute("SELECT coins FROM users WHERE id=?", (order["buyer_id"],)).fetchone()
            buyer_coins = int(buyer["coins"] if buyer else 0)
            final_price = int(order["final_price"] or 0)

            if buyer_coins < final_price:
                db.close()
                flash("校園錢包金幣不足，請改用其他付款方式或先取得更多金幣", "danger")
                return redirect(f"/trade-order/{order_id}/pay")

            db.execute("UPDATE users SET coins = coins - ? WHERE id=?", (final_price, order["buyer_id"]))
            db.execute("UPDATE users SET coins = coins + ? WHERE id=?", (final_price, order["seller_id"]))

        now_text = datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            """
            UPDATE trade_orders
            SET payment_status='已付款',
                payment_method=?,
                payment_note=?,
                paid_at=?,
                updated_at=?
            WHERE id=?
            """,
            (payment_method, payment_note, now_text, now_text, order_id)
        )
        db.commit()
        db.close()

        create_notification_realtime(
            order["seller_id"],
            f"💳 買家已完成付款：{order['title']}，請與買家確認面交。",
            f"/product/{order['product_id']}"
        )

        flash("付款完成，已通知賣家", "success")
        return redirect(f"/product/{order['product_id']}")

    db.close()
    return render_template("pay_order.html", order=order)


@app.route("/trade-order/<int:order_id>/status", methods=["POST"])
def update_trade_order_status(order_id):
    if "user_id" not in session:
        return redirect("/login")

    status = request.form.get("status")
    allowed_statuses = ["等待面交", "已面交", "已完成", "取消交易"]
    if status not in allowed_statuses:
        flash("交易狀態不正確", "danger")
        return redirect("/")

    db = get_db()
    order = db.execute("SELECT * FROM trade_orders WHERE id=?", (order_id,)).fetchone()
    if order is None:
        db.close()
        return "找不到交易訂單"

    if session["user_id"] not in [order["seller_id"], order["buyer_id"]] and not is_admin():
        db.close()
        return "你沒有權限更新此交易"

    db.execute(
        """
        UPDATE trade_orders
        SET status=?, updated_at=?
        WHERE id=?
        """,
        (status, datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"), order_id)
    )

    if status == "已完成":
        db.execute("UPDATE products SET is_completed=1 WHERE id=?", (order["product_id"],))

    db.commit()
    product = db.execute("SELECT title FROM products WHERE id=?", (order["product_id"],)).fetchone()
    db.close()

    product_title = product["title"] if product else "商品"
    for uid in [order["seller_id"], order["buyer_id"]]:
        if uid != session["user_id"]:
            create_notification_realtime(
                uid,
                f"📦 交易訂單狀態已更新為「{status}」：{product_title}",
                f"/product/{order['product_id']}"
            )

    flash("交易狀態已更新", "success")
    return redirect(f"/product/{order['product_id']}")


@app.route("/admin/review-report/<int:report_id>", methods=["POST"])
def admin_review_report(report_id):
    if not require_admin_role("review_admin"):
        return "權限不足：只有審核管理員或超級管理員可以處理檢舉"

    action = request.form.get("action")
    admin_note = request.form.get("admin_note", "").strip()

    db = get_db()
    report = db.execute(
        """
        SELECT reports.*, products.title, products.user_id AS seller_id
        FROM reports
        LEFT JOIN products ON reports.product_id = products.id
        WHERE reports.id=?
        """,
        (report_id,)
    ).fetchone()

    if report is None:
        db.close()
        return "找不到檢舉資料"

    status = "已駁回"
    if action == "block_product":
        status = "已封鎖商品"
        db.execute("UPDATE products SET is_paused=1 WHERE id=?", (report["product_id"],))
    elif action == "warn_user":
        status = "已警告使用者"
        if report["seller_id"]:
            create_notification_realtime(
                report["seller_id"],
                f"⚠️ 管理員警告：你的商品「{report['title']}」被檢舉，請確認內容是否違規。{admin_note}",
                f"/product/{report['product_id']}"
            )
    elif action == "approve":
        status = "已審核"

    db.execute(
        "UPDATE reports SET status=?, admin_note=? WHERE id=?",
        (status, admin_note, report_id)
    )
    db.commit()
    db.close()

    flash("檢舉審核已更新", "success")
    return redirect("/admin")


@app.route("/admin")
def admin():
    if not is_admin():
        return "你不是管理員，無法進入後台"

    db = get_db()
    ensure_admin_feature_columns(db)
    db.commit()

    keyword = request.args.get("keyword", "").strip()
    report_status = request.args.get("report_status", "").strip()
    product_status = request.args.get("product_status", "").strip()

    user_sql = "SELECT * FROM users WHERE 1=1"
    user_params = []
    if keyword:
        user_sql += " AND (username LIKE ? OR email LIKE ?)"
        user_params.extend([f"%{keyword}%", f"%{keyword}%"])
    user_sql += " ORDER BY id DESC LIMIT 200"
    users = db.execute(user_sql, user_params).fetchall()

    report_sql = """
        SELECT
            reports.*,
            users.username,
            products.title
        FROM reports
        LEFT JOIN users ON reports.reporter_id = users.id
        LEFT JOIN products ON reports.product_id = products.id
        WHERE 1=1
    """
    report_params = []
    if report_status:
        report_sql += " AND IFNULL(reports.status, '待審核')=?"
        report_params.append(report_status)
    if keyword:
        report_sql += " AND (products.title LIKE ? OR users.username LIKE ? OR reports.reason LIKE ?)"
        report_params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    report_sql += " ORDER BY reports.id DESC LIMIT 200"
    reports = db.execute(report_sql, report_params).fetchall()

    product_sql = """
        SELECT products.*, users.username
        FROM products
        LEFT JOIN users ON products.user_id = users.id
        WHERE 1=1
    """
    product_params = []
    if keyword:
        product_sql += " AND (products.title LIKE ? OR users.username LIKE ? OR products.category LIKE ?)"
        product_params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if product_status == "hidden":
        product_sql += " AND IFNULL(products.admin_hidden, 0)=1"
    elif product_status == "visible":
        product_sql += " AND IFNULL(products.admin_hidden, 0)=0"
    elif product_status == "paused":
        product_sql += " AND IFNULL(products.is_paused, 0)=1"
    elif product_status == "completed":
        product_sql += " AND IFNULL(products.is_completed, 0)=1"
    product_sql += " ORDER BY products.id DESC LIMIT 200"
    products = db.execute(product_sql, product_params).fetchall()

    bids = db.execute(
        """
        SELECT bids.*, users.username, products.title
        FROM bids
        LEFT JOIN users ON bids.user_id = users.id
        LEFT JOIN products ON bids.product_id = products.id
        ORDER BY bids.id DESC
        LIMIT 200
        """
    ).fetchall()

    messages = db.execute(
        """
        SELECT chat_messages.*, users.username, products.title
        FROM chat_messages
        LEFT JOIN users ON chat_messages.user_id = users.id
        LEFT JOIN products ON chat_messages.product_id = products.id
        ORDER BY chat_messages.id DESC
        LIMIT 200
        """
    ).fetchall()

    orders = db.execute(
        """
        SELECT trade_orders.*, products.title, seller.username AS seller_name, buyer.username AS buyer_name
        FROM trade_orders
        LEFT JOIN products ON trade_orders.product_id = products.id
        LEFT JOIN users AS seller ON trade_orders.seller_id = seller.id
        LEFT JOIN users AS buyer ON trade_orders.buyer_id = buyer.id
        ORDER BY trade_orders.id DESC
        LIMIT 200
        """
    ).fetchall()

    questions = db.execute(
        """
        SELECT product_questions.*, products.title, users.username AS asker_name
        FROM product_questions
        LEFT JOIN products ON product_questions.product_id = products.id
        LEFT JOIN users ON product_questions.asker_id = users.id
        ORDER BY product_questions.id DESC
        LIMIT 200
        """
    ).fetchall()

    coin_logs = db.execute(
        """
        SELECT coin_logs.*, users.username
        FROM coin_logs
        LEFT JOIN users ON coin_logs.user_id = users.id
        ORDER BY coin_logs.id DESC
        LIMIT 200
        """
    ).fetchall()

    announcements = db.execute(
        """
        SELECT *
        FROM announcements
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    stats = {
        "total_users": db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"],
        "banned_users": db.execute("SELECT COUNT(*) AS count FROM users WHERE IFNULL(is_banned, 0)=1").fetchone()["count"],
        "total_products": db.execute("SELECT COUNT(*) AS count FROM products").fetchone()["count"],
        "hidden_products": db.execute("SELECT COUNT(*) AS count FROM products WHERE IFNULL(admin_hidden, 0)=1").fetchone()["count"],
        "today_products": db.execute("SELECT COUNT(*) AS count FROM products WHERE date(end_time) >= date('now', '+8 hours')").fetchone()["count"],
        "total_bids": db.execute("SELECT COUNT(*) AS count FROM bids").fetchone()["count"],
        "pending_reports": db.execute("SELECT COUNT(*) AS count FROM reports WHERE IFNULL(status, '待審核')='待審核'").fetchone()["count"],
        "completed_orders": db.execute("SELECT COUNT(*) AS count FROM trade_orders WHERE status='已完成'").fetchone()["count"],
        "total_sales": db.execute("SELECT IFNULL(SUM(final_price), 0) AS total FROM trade_orders WHERE status IN ('已完成', '已面交', '等待面交')").fetchone()["total"],
    }

    db.close()

    return render_template(
        "admin.html",
        users=users,
        products=products,
        bids=bids,
        messages=messages,
        reports=reports,
        orders=orders,
        questions=questions,
        coin_logs=coin_logs,
        announcements=announcements,
        stats=stats,
        keyword=keyword,
        report_status=report_status,
        product_status=product_status,
        admin_roles=ADMIN_ROLES
    )


@app.route("/admin/delete-product/<int:id>")
def admin_delete_product(id):
    """保留舊網址，但改成後台下架，不直接刪除資料。"""
    if not require_admin_role("review_admin"):
        return "權限不足：只有審核管理員或超級管理員可以下架商品"

    db = get_db()
    ensure_admin_feature_columns(db)

    db.execute(
        """
        UPDATE products
        SET admin_hidden=1,
            admin_hidden_reason='管理員下架',
            admin_hidden_at=?
        WHERE id=?
        """,
        (now_text(), id)
    )

    db.commit()
    db.close()

    flash("管理員已下架商品，資料仍保留", "success")
    return redirect("/admin")


@app.route("/admin/hide-product/<int:id>")
def admin_hide_product(id):
    return admin_delete_product(id)


@app.route("/admin/restore-product/<int:id>")
def admin_restore_product(id):
    if not require_admin_role("review_admin"):
        return "權限不足：只有審核管理員或超級管理員可以恢復商品"

    db = get_db()
    ensure_admin_feature_columns(db)

    db.execute(
        """
        UPDATE products
        SET admin_hidden=0,
            admin_hidden_reason=NULL,
            admin_hidden_at=NULL
        WHERE id=?
        """,
        (id,)
    )

    db.commit()
    db.close()

    flash("商品已恢復上架", "success")
    return redirect("/admin")


@app.route("/admin/ban-user/<int:id>", methods=["POST"])
def admin_ban_user(id):
    if not is_super_admin():
        return "權限不足：只有超級管理員可以停權使用者"

    if id == session.get("user_id"):
        flash("不能停權自己", "danger")
        return redirect("/admin")

    reason = request.form.get("ban_reason", "違反平台規範").strip() or "違反平台規範"

    db = get_db()
    ensure_admin_feature_columns(db)

    db.execute(
        """
        UPDATE users
        SET is_banned=1,
            ban_reason=?
        WHERE id=?
        """,
        (reason, id)
    )

    db.commit()
    db.close()

    flash("使用者已停權", "success")
    return redirect("/admin")


@app.route("/admin/unban-user/<int:id>", methods=["POST"])
def admin_unban_user(id):
    if not is_super_admin():
        return "權限不足：只有超級管理員可以解封使用者"

    db = get_db()
    ensure_admin_feature_columns(db)

    db.execute(
        """
        UPDATE users
        SET is_banned=0,
            ban_reason=NULL
        WHERE id=?
        """,
        (id,)
    )

    db.commit()
    db.close()

    flash("使用者已解封", "success")
    return redirect("/admin")


@app.route("/admin/add-announcement", methods=["POST"])
def admin_add_announcement():
    if not is_super_admin():
        return "權限不足：只有超級管理員可以新增公告"

    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    if not title or not content:
        flash("公告標題與內容都必填", "danger")
        return redirect("/admin")

    db = get_db()
    ensure_admin_feature_columns(db)

    db.execute(
        """
        INSERT INTO announcements(title, content, is_active, created_at)
        VALUES (?, ?, 1, ?)
        """,
        (title, content, now_text())
    )

    db.commit()
    db.close()

    flash("公告已新增", "success")
    return redirect("/admin")


@app.route("/admin/toggle-announcement/<int:id>", methods=["POST"])
def admin_toggle_announcement(id):
    if not is_super_admin():
        return "權限不足：只有超級管理員可以切換公告"

    db = get_db()
    ensure_admin_feature_columns(db)

    announcement = db.execute(
        "SELECT * FROM announcements WHERE id=?",
        (id,)
    ).fetchone()

    if announcement:
        new_status = 0 if int(announcement["is_active"] or 0) == 1 else 1
        db.execute(
            "UPDATE announcements SET is_active=? WHERE id=?",
            (new_status, id)
        )
        db.commit()

    db.close()

    flash("公告狀態已更新", "success")
    return redirect("/admin")


@app.route("/admin/delete-announcement/<int:id>", methods=["POST"])
def admin_delete_announcement(id):
    if not is_super_admin():
        return "權限不足：只有超級管理員可以刪除公告"

    db = get_db()
    db.execute("DELETE FROM announcements WHERE id=?", (id,))
    db.commit()
    db.close()

    flash("公告已刪除", "success")
    return redirect("/admin")



@app.route("/admin/set-admin-role/<int:id>", methods=["POST"])
def admin_set_admin_role(id):
    if not is_super_admin():
        return "權限不足：只有超級管理員可以設定管理員權限"

    role = request.form.get("admin_role", "user").strip()

    allowed_roles = ["user", "super_admin", "support_admin", "review_admin"]

    if role not in allowed_roles:
        flash("不合法的管理員角色", "danger")
        return redirect("/admin")

    if id == session.get("user_id") and role != "super_admin":
        flash("不能把自己的超級管理員權限移除", "danger")
        return redirect("/admin")

    db = get_db()
    ensure_admin_feature_columns(db)

    is_admin_value = 0 if role == "user" else 1

    db.execute(
        """
        UPDATE users
        SET admin_role=?,
            is_admin=?
        WHERE id=?
        """,
        (
            role,
            is_admin_value,
            id
        )
    )

    db.commit()
    db.close()

    flash("管理員權限已更新", "success")
    return redirect("/admin")


@app.route("/admin/delete-user/<int:id>")
def admin_delete_user(id):
    if not is_super_admin():
        return "權限不足：只有超級管理員可以刪除使用者"

    if id == session.get("user_id"):
        flash("不能刪除自己", "danger")
        return redirect("/admin")

    db = get_db()

    db.execute("DELETE FROM users WHERE id=?", (id,))

    db.commit()
    db.close()

    flash("管理員已刪除使用者", "success")
    return redirect("/admin")




@app.route("/admin/delete-message/<int:id>")
def admin_delete_message(id):
    if not require_admin_role("support_admin"):
        return "權限不足：只有客服管理員或超級管理員可以刪除聊天室留言"

    db = get_db()

    db.execute(
        "DELETE FROM chat_messages WHERE id=?",
        (id,)
    )

    db.commit()
    db.close()

    flash("管理員已刪除聊天室留言", "success")
    return redirect("/admin")

@app.route("/live")
def live():

    db = get_db()

    products = db.execute(
        """
        SELECT products.*, users.username
        FROM products
        JOIN users
        ON products.user_id = users.id
        WHERE IFNULL(products.is_completed, 0)=0
        AND IFNULL(products.is_paused, 0)=0
        ORDER BY products.id DESC
        """
    ).fetchall()

    db.close()

    return render_template(
        "live.html",
        products=products
    )


@app.route("/pin-product/<int:product_id>")
def pin_product(product_id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    # 確保欄位存在
    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN is_pinned INTEGER DEFAULT 0"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN pin_expire_time TEXT"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN special_frame INTEGER DEFAULT 0"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

    product = db.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if product["user_id"] != session["user_id"]:
        db.close()
        flash("你不能置頂別人的商品", "danger")
        return redirect("/my-products")

    if (
        product["is_pinned"] == 1
        and product["pin_expire_time"]
        and datetime.strptime(product["pin_expire_time"], "%Y-%m-%d %H:%M:%S")
        < datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)
    ):
        db.execute(
            """
            UPDATE products
            SET is_pinned=0,
                pin_expire_time=NULL
            WHERE id=?
            """,
            (product_id,)
        )
        db.commit()

        product = db.execute(
            """
            SELECT *
            FROM products
            WHERE id=?
            """,
            (product_id,)
        ).fetchone()

    if product["is_pinned"] == 1:
        db.close()
        flash("此商品已經置頂中", "danger")
        return redirect("/my-products")

    user = db.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (session["user_id"],)
    ).fetchone()

    if user is None:
        db.close()
        session.clear()
        flash("登入狀態已失效，請重新登入", "danger")
        return redirect("/login")

    if user["coins"] < 500:
        db.close()
        flash("金幣不足，置頂商品需要 500 金幣", "danger")
        return redirect("/my-products")

    new_coins = user["coins"] - 500

    pin_expire_time = (
        datetime.now(TAIWAN_TIMEZONE)
        + timedelta(hours=24)
    ).strftime("%Y-%m-%d %H:%M:%S")

    db.execute(
        """
        UPDATE users
        SET coins=?
        WHERE id=?
        """,
        (
            new_coins,
            session["user_id"]
        )
    )

    db.execute(
        """
        UPDATE products
        SET is_pinned=1,
            pin_expire_time=?
        WHERE id=?
        """,
        (
            pin_expire_time,
            product_id
        )
    )

    db.commit()
    db.close()

    session["coins"] = new_coins

    flash("🚀 商品已成功置頂 24 小時，已扣除 500 金幣！", "success")
    return redirect("/my-products")


@app.route("/buy-frame/<int:product_id>")
def buy_frame(product_id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    try:
        db.execute(
            "ALTER TABLE products ADD COLUMN special_frame INTEGER DEFAULT 0"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

    product = db.execute(
        """
        SELECT *
        FROM products
        WHERE id=?
        """,
        (product_id,)
    ).fetchone()

    if product is None:
        db.close()
        return "找不到商品"

    if product["user_id"] != session["user_id"]:
        db.close()
        flash("你不能幫別人的商品購買特殊框", "danger")
        return redirect("/my-products")

    if product["special_frame"] == 1:
        db.close()
        flash("此商品已經有特殊框了", "danger")
        return redirect("/my-products")

    user = db.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (session["user_id"],)
    ).fetchone()

    if user is None:
        db.close()
        session.clear()
        flash("登入狀態已失效，請重新登入", "danger")
        return redirect("/login")

    if user["coins"] < 100:
        db.close()
        flash("金幣不足，商品特殊框需要 100 金幣", "danger")
        return redirect("/my-products")

    new_coins = user["coins"] - 100

    db.execute(
        """
        UPDATE users
        SET coins=?
        WHERE id=?
        """,
        (
            new_coins,
            session["user_id"]
        )
    )

    db.execute(
        """
        UPDATE products
        SET special_frame=1
        WHERE id=?
        """,
        (product_id,)
    )

    db.commit()
    db.close()

    session["coins"] = new_coins

    flash("✨ 商品已成功套用特殊框，已扣除 100 金幣！", "success")
    return redirect("/my-products")


@app.route("/favorite/<int:product_id>")
def favorite(product_id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    existing = db.execute(
        """
        SELECT * FROM favorites
        WHERE user_id=? AND product_id=?
        """,
        (session["user_id"], product_id)
    ).fetchone()

    if existing:
        db.execute(
            """
            DELETE FROM favorites
            WHERE user_id=? AND product_id=?
            """,
            (session["user_id"], product_id)
        )

        flash("已取消收藏", "success")

    else:
        db.execute(
            """
            INSERT INTO favorites(user_id, product_id, created_at)
            VALUES (?, ?, ?)
            """,
            (
                session["user_id"],
                product_id,
                datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            )
        )

        flash("已加入收藏", "success")

    favorite_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM favorites
        WHERE user_id=?
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    popular_products = db.execute(
        """
        SELECT products.user_id
        FROM products
        JOIN favorites
        ON products.id = favorites.product_id
        WHERE products.id=?
        GROUP BY products.id
        HAVING COUNT(favorites.id) >= 50
        """,
        (product_id,)
    ).fetchone()

    db.commit()
    db.close()

    if favorite_count >= 1:
        unlock_achievement(
            session["user_id"],
            "🏆 第一次收藏",
            "收藏了第一件商品"
        )

    if favorite_count >= 100:
        unlock_achievement(
            session["user_id"],
            "❤️ 收藏王",
            "收藏超過 100 件商品"
        )

    if popular_products:
        unlock_achievement(
            popular_products["user_id"],
            "👀 人氣商品",
            "商品被收藏超過 50 次"
        )

    check_all_achievements(session["user_id"])

    return redirect(request.referrer or "/")


@app.route("/my-favorites")
def my_favorites():

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    favorites = db.execute(
        """
        SELECT
            products.*,
            COUNT(f2.id) AS favorite_count
        FROM favorites
        JOIN products
        ON favorites.product_id = products.id
        LEFT JOIN favorites AS f2
        ON products.id = f2.product_id
        WHERE favorites.user_id=?
        GROUP BY products.id
        ORDER BY favorites.id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    db.close()

    return render_template(
        "my_favorites.html",
        favorites=favorites
    )


def initialize_app_for_deploy():
    """初始化資料庫與背景提醒任務。

    Gunicorn / Render 部署時不會執行 __main__，所以初始化必須放在匯入時也會跑的位置。
    因 Procfile 使用 -w 1，只會啟動一個背景提醒任務。
    """
    create_reports_table()

    if not getattr(app, "_ending_task_started", False):
        socketio.start_background_task(ending_reminder_background_task)
        app._ending_task_started = True


initialize_app_for_deploy()


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=debug
    )
