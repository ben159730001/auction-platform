from pathlib import Path
import shutil
from datetime import datetime

APP_PATH = Path("app.py")
if not APP_PATH.exists():
    raise SystemExit("找不到 app.py，請把本檔放在 app.py 同一層資料夾後再執行。")

text = APP_PATH.read_text(encoding="utf-8")
backup = APP_PATH.with_name(f"app_backup_before_antifraud_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py")
shutil.copy2(APP_PATH, backup)

def insert_once(text, marker, block, before=False):
    if block.strip() in text:
        return text
    if marker not in text:
        raise SystemExit(f"找不到插入位置：{marker}")
    if before:
        return text.replace(marker, block + "\n\n" + marker, 1)
    return text.replace(marker, marker + "\n" + block + "\n", 1)

if "import secrets" not in text:
    text = text.replace("import random\n", "import random\nimport secrets\n", 1)

schema_block = r'''
    # 防詐騙機制欄位：Email 驗證、手機驗證、風險分數、IP、異常出價
    for sql in [
        "ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN email_verify_token TEXT",
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "ALTER TABLE users ADD COLUMN phone_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN risk_score INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN created_at TEXT",
        "ALTER TABLE users ADD COLUMN last_ip TEXT",
        "ALTER TABLE bids ADD COLUMN ip_address TEXT",
        "ALTER TABLE bids ADD COLUMN is_suspicious INTEGER DEFAULT 0",
        "ALTER TABLE bids ADD COLUMN risk_reason TEXT"
    ]:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass

    try:
        db.execute(
            """
            UPDATE users
            SET created_at=?
            WHERE created_at IS NULL OR created_at=''
            """,
            (now_text(),)
        )
    except sqlite3.OperationalError:
        pass

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reason TEXT,
            score_added INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
'''
if "CREATE TABLE IF NOT EXISTS risk_logs" not in text:
    marker = "    # 後台進階功能：停權、商品下架、金幣紀錄、公告\n    ensure_admin_feature_columns(db)"
    if marker not in text:
        raise SystemExit("找不到 create_reports_table 的後台進階功能插入點")
    text = text.replace(marker, schema_block + "\n" + marker, 1)

helper_block = r'''
def send_verify_email(email, token):
    """
    寄出 Email 驗證信。
    如果沒有設定 MAIL_USERNAME / MAIL_PASSWORD，會進入展示模式：
    不寄信，但會在 Render logs / 終端機印出驗證連結。
    """
    mail_username = os.environ.get("MAIL_USERNAME")
    mail_password = os.environ.get("MAIL_PASSWORD")
    mail_server = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    mail_port = int(os.environ.get("MAIL_PORT", "587"))

    verify_link = f"{request.host_url.rstrip('/')}/verify-email/{token}"

    if not mail_username or not mail_password:
        print("Email 驗證連結：", verify_link)
        return False

    subject = "校園拍賣平台 Email 驗證"
    body = f"""請點擊以下連結完成 Email 驗證：

{verify_link}

如果不是你本人註冊，請忽略此信件。
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


def add_risk_log(db, user_id, reason, score):
    """增加使用者風險分數，超過 80 自動停權。"""
    db.execute(
        """
        INSERT INTO risk_logs(user_id, reason, score_added, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, reason, score, now_text())
    )

    db.execute(
        """
        UPDATE users
        SET risk_score = IFNULL(risk_score, 0) + ?
        WHERE id=?
        """,
        (score, user_id)
    )

    total = db.execute(
        "SELECT IFNULL(risk_score, 0) AS risk_score FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if total and int(total["risk_score"] or 0) >= 80:
        db.execute(
            """
            UPDATE users
            SET is_banned=1,
                ban_reason='系統偵測高風險異常行為'
            WHERE id=?
            """,
            (user_id,)
        )


def check_bid_risk(db, user_id, product_id, bid_price):
    """出價前風控：Email 驗證、新帳號限制、短時間大量出價、同 IP 多帳號。"""
    user = db.execute(
        "SELECT * FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if user is None:
        return False, "找不到使用者"

    if "email_verified" in user.keys() and int(user["email_verified"] or 0) != 1:
        return False, "請先完成 Email 驗證才能出價"

    if "risk_score" in user.keys() and int(user["risk_score"] or 0) >= 80:
        return False, "你的帳號風險過高，已限制出價"

    now = datetime.now(TAIWAN_TIMEZONE).replace(tzinfo=None)
    created_at_text = user["created_at"] if "created_at" in user.keys() else None

    if created_at_text:
        try:
            created_at = datetime.strptime(created_at_text, "%Y-%m-%d %H:%M:%S")
            account_age = now - created_at

            if account_age < timedelta(days=1):
                today_bid_count = db.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM bids
                    WHERE user_id=?
                    AND datetime(created_at) >= datetime('now', '+8 hours', '-24 hours')
                    """,
                    (user_id,)
                ).fetchone()["count"]

                if today_bid_count >= 3:
                    add_risk_log(db, user_id, "新帳號 24 小時內出價過多", 20)
                    return False, "新帳號 24 小時內最多只能出價 3 次"
        except Exception:
            pass

    five_min_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM bids
        WHERE user_id=?
        AND datetime(created_at) >= datetime('now', '+8 hours', '-5 minutes')
        """,
        (user_id,)
    ).fetchone()["count"]

    if five_min_count >= 10:
        add_risk_log(db, user_id, "5 分鐘內大量出價", 30)
        return False, "出價太頻繁，請稍後再試"

    same_ip_users = db.execute(
        """
        SELECT COUNT(DISTINCT user_id) AS count
        FROM bids
        WHERE ip_address=?
        AND datetime(created_at) >= datetime('now', '+8 hours', '-24 hours')
        """,
        (request.remote_addr,)
    ).fetchone()["count"]

    if same_ip_users >= 5:
        add_risk_log(db, user_id, "同 IP 多帳號出價", 30)
        return False, "系統偵測到異常出價行為"

    return True, None

'''
if "def send_verify_email(email, token):" not in text:
    text = insert_once(text, "\ndef get_reset_code_age_minutes(created_at_text):", helper_block, before=True)

old_register = '''        cursor = db.execute(
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
        )'''
new_register = '''        email_verify_token = secrets.token_urlsafe(32)

        cursor = db.execute(
            """
            INSERT INTO users(
                username,
                password,
                email,
                coins,
                email_verified,
                email_verify_token,
                created_at
            )
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                username,
                password,
                email,
                2000,
                email_verify_token,
                now_text()
            )
        )

        try:
            send_verify_email(email, email_verify_token)
        except Exception as exc:
            print("send verify email error:", exc)'''
if old_register in text and "email_verify_token = secrets.token_urlsafe(32)" not in text:
    text = text.replace(old_register, new_register, 1)

text = text.replace(
    '''        flash(
            "🎉 註冊成功！已獲得 2000 金幣",
            "success"
        )''',
    '''        flash(
            "🎉 註冊成功！已獲得 2000 金幣，請先完成 Email 驗證才能出價",
            "success"
        )'''
)

verify_route = '''
@app.route("/verify-email/<token>")
def verify_email(token):
    db = get_db()

    user = db.execute(
        """
        SELECT *
        FROM users
        WHERE email_verify_token=?
        """,
        (token,)
    ).fetchone()

    if user is None:
        db.close()
        flash("驗證連結無效或已使用", "danger")
        return redirect("/login")

    db.execute(
        """
        UPDATE users
        SET email_verified=1,
            email_verify_token=NULL
        WHERE id=?
        """,
        (user["id"],)
    )

    db.commit()
    db.close()

    flash("Email 驗證成功，現在可以出價了！", "success")
    return redirect("/login")

'''
if '@app.route("/verify-email/<token>")' not in text:
    text = insert_once(text, '@app.route("/login", methods=["GET", "POST"])', verify_route, before=True)

risk_check = '''
    allowed, risk_message = check_bid_risk(
        db,
        session["user_id"],
        id,
        bid_price
    )

    if not allowed:
        db.commit()
        db.close()
        flash(risk_message, "danger")
        return redirect(f"/product/{id}")
'''
min_block = '''    if bid_price < min_bid_price:
        db.close()
        flash(f"最低出價需為 NT$ {min_bid_price}，目前價格需至少加 {bid_increment} 元", "danger")
        return redirect(f"/product/{id}")'''
if risk_check.strip() not in text and min_block in text:
    text = text.replace(min_block, min_block + "\n" + risk_check, 1)

old_bid_insert = '''    db.execute(
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
    )'''
new_bid_insert = '''    db.execute(
        """
        INSERT INTO bids(product_id, user_id, bid_price, created_at, ip_address)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            id,
            session["user_id"],
            bid_price,
            datetime.now(TAIWAN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            request.remote_addr
        )
    )'''
if old_bid_insert in text:
    text = text.replace(old_bid_insert, new_bid_insert, 1)

product_update = '''    db.execute(
        """
        UPDATE products
        SET current_price=?
        WHERE id=?
        """,
        (bid_price, id)
    )'''
last_ip_update = '''
    db.execute(
        """
        UPDATE users
        SET last_ip=?
        WHERE id=?
        """,
        (request.remote_addr, session["user_id"])
    )'''
if last_ip_update.strip() not in text and product_update in text:
    text = text.replace(product_update, product_update + "\n" + last_ip_update, 1)

risk_admin_queries = '''
    risk_logs = db.execute(
        """
        SELECT risk_logs.*, users.username, users.email
        FROM risk_logs
        LEFT JOIN users ON risk_logs.user_id = users.id
        ORDER BY risk_logs.id DESC
        LIMIT 200
        """
    ).fetchall()

    risk_users = db.execute(
        """
        SELECT *
        FROM users
        WHERE IFNULL(risk_score, 0) > 0
        ORDER BY risk_score DESC
        LIMIT 100
        """
    ).fetchall()
'''
if "risk_logs = db.execute(" not in text:
    stats_marker = "    stats = {\n"
    if stats_marker in text:
        text = text.replace(stats_marker, risk_admin_queries + "\n" + stats_marker, 1)

if "risk_logs=risk_logs," not in text:
    text = text.replace(
        "        admin_roles=ADMIN_ROLES\n",
        "        admin_roles=ADMIN_ROLES,\n        risk_logs=risk_logs,\n        risk_users=risk_users\n",
        1
    )

APP_PATH.write_text(text, encoding="utf-8")
print("已完成 app.py 防詐騙修改")
print(f"原檔備份：{backup.name}")
print("請再把 admin_risk_snippet.html 的內容貼到 templates/admin.html 適合位置。")
