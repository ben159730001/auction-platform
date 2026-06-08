import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

columns = [
    "trade_method TEXT DEFAULT '面交'",
    "meetup_time TEXT DEFAULT '平日晚上'",
    "payment_method TEXT DEFAULT '現金'"
]

for column in columns:
    try:
        cursor.execute(f"ALTER TABLE products ADD COLUMN {column}")
    except sqlite3.OperationalError:
        pass

conn.commit()
conn.close()

print("商品規格欄位已更新完成")
