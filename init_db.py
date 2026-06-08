import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    email TEXT,
    password TEXT,
    coins INTEGER DEFAULT 100,
    is_admin INTEGER DEFAULT 0,
    avatar TEXT,
    banner TEXT,
    last_checkin TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    start_price INTEGER,
    current_price INTEGER,
    image TEXT,
    image1 TEXT,
    image2 TEXT,
    image3 TEXT,
    image4 TEXT,
    image5 TEXT,
    description TEXT,
    category TEXT,
    condition TEXT DEFAULT '二手',
    end_time TEXT,
    user_id INTEGER,
    views INTEGER DEFAULT 0,
    is_pinned INTEGER DEFAULT 0,
    pin_expire_time TEXT,
    special_frame INTEGER DEFAULT 0,
    is_completed INTEGER DEFAULT 0,
    is_paused INTEGER DEFAULT 0,
    meet_location TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    user_id INTEGER,
    bid_price INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    user_id INTEGER,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS chat_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    user_id INTEGER,
    reaction TEXT,
    created_at TEXT,
    UNIQUE(message_id, user_id, reaction)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    link TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    blocked_user_id INTEGER,
    blocked_by INTEGER,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    reviewer_id INTEGER,
    target_user_id INTEGER,
    rating INTEGER,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS auto_bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    user_id INTEGER,
    max_price INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS private_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER,
    receiver_id INTEGER,
    product_id INTEGER,
    message TEXT,
    image TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product_id INTEGER,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS browsing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product_id INTEGER,
    viewed_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    reporter_id INTEGER,
    reason TEXT,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    discount INTEGER,
    is_used INTEGER DEFAULT 0,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    description TEXT,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reset_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    code TEXT,
    is_used INTEGER DEFAULT 0,
    created_at TEXT
)
""")


cursor.execute("""
CREATE TABLE IF NOT EXISTS trade_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER UNIQUE,
    seller_id INTEGER,
    buyer_id INTEGER,
    final_price INTEGER,
    status TEXT DEFAULT '等待面交',
    payment_status TEXT DEFAULT '未付款',
    payment_method TEXT,
    payment_note TEXT,
    paid_at TEXT,
    created_at TEXT,
    updated_at TEXT
)
""")

for payment_column in [
    "payment_status TEXT DEFAULT '未付款'",
    "payment_method TEXT",
    "payment_note TEXT",
    "paid_at TEXT"
]:
    try:
        cursor.execute(f"ALTER TABLE trade_orders ADD COLUMN {payment_column}")
    except sqlite3.OperationalError:
        pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS product_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    asker_id INTEGER,
    question TEXT,
    answer TEXT,
    created_at TEXT,
    answered_at TEXT
)
""")

try:
    cursor.execute("ALTER TABLE products ADD COLUMN ending_reminder_sent INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE reports ADD COLUMN status TEXT DEFAULT '待審核'")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE reports ADD COLUMN admin_note TEXT")
except sqlite3.OperationalError:
    pass

conn.commit()
conn.close()

print("資料庫建立完成：已加入多圖片、商品狀態、留言反應、使用者等級支援")
