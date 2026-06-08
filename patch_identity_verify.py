from pathlib import Path
import shutil
from datetime import datetime

APP_PATH = Path("app.py")
if not APP_PATH.exists():
    raise SystemExit("找不到 app.py，請把本檔放在 app.py 同一層資料夾後再執行。")

text = APP_PATH.read_text(encoding="utf-8")
backup = APP_PATH.with_name(
    f"app_backup_before_identity_verify_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
)
shutil.copy2(APP_PATH, backup)

schema_block = '''
    # 身分驗證功能：使用者上傳學生證/證件，管理員審核
    for sql in [
        "ALTER TABLE users ADD COLUMN identity_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN identity_verify_status TEXT DEFAULT '未申請'",
        "ALTER TABLE users ADD COLUMN identity_reject_reason TEXT",
        "ALTER TABLE users ADD COLUMN identity_verified_at TEXT"
    ]:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            real_name TEXT,
            student_id TEXT,
            department TEXT,
            document_image TEXT,
            status TEXT DEFAULT '待審核',
            reject_reason TEXT,
            created_at TEXT,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )
        """
    )
'''

if "CREATE TABLE IF NOT EXISTS identity_verifications" not in text:
    marker = "    # 後台進階功能：停權、商品下架、金幣紀錄、公告\n    ensure_admin_feature_columns(db)"
    if marker not in text:
        raise SystemExit("找不到 create_reports_table 裡面的 ensure_admin_feature_columns 插入點")
    text = text.replace(marker, schema_block + "\n" + marker, 1)

identity_routes = '''
@app.route("/identity-verify", methods=["GET", "POST"])
def identity_verify():
    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    user = db.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    latest = db.execute(
        """
        SELECT *
        FROM identity_verifications
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["user_id"],)
    ).fetchone()

    if request.method == "POST":
        real_name = request.form.get("real_name", "").strip()
        student_id = request.form.get("student_id", "").strip()
        department = request.form.get("department", "").strip()
        document_file = request.files.get("document_image")

        if not real_name or not student_id or not department:
            db.close()
            flash("請完整填寫真實姓名、學號與系所", "danger")
            return redirect("/identity-verify")

        image_path = save_uploaded_image(document_file)

        if not image_path:
            db.close()
            flash("請上傳學生證或身分證明圖片", "danger")
            return redirect("/identity-verify")

        db.execute(
            """
            INSERT INTO identity_verifications(
                user_id,
                real_name,
                student_id,
                department,
                document_image,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, '待審核', ?)
            """,
            (
                session["user_id"],
                real_name,
                student_id,
                department,
                image_path,
                now_text()
            )
        )

        db.execute(
            """
            UPDATE users
            SET identity_verify_status='待審核',
                identity_reject_reason=NULL
            WHERE id=?
            """,
            (session["user_id"],)
        )

        db.commit()
        db.close()

        flash("身分驗證已送出，請等待管理員審核", "success")
        return redirect("/identity-verify")

    db.close()

    return render_template(
        "identity_verify.html",
        user=user,
        latest=latest
    )


@app.route("/admin/review-identity/<int:verify_id>", methods=["POST"])
def admin_review_identity(verify_id):
    if not require_admin_role("review_admin"):
        return "權限不足：只有審核管理員或超級管理員可以審核身分驗證"

    action = request.form.get("action")
    reject_reason = request.form.get("reject_reason", "").strip()

    db = get_db()

    verify = db.execute(
        """
        SELECT *
        FROM identity_verifications
        WHERE id=?
        """,
        (verify_id,)
    ).fetchone()

    if verify is None:
        db.close()
        return "找不到身分驗證申請"

    if action == "approve":
        db.execute(
            """
            UPDATE identity_verifications
            SET status='已通過',
                reject_reason=NULL,
                reviewed_at=?,
                reviewed_by=?
            WHERE id=?
            """,
            (
                now_text(),
                session["user_id"],
                verify_id
            )
        )

        db.execute(
            """
            UPDATE users
            SET identity_verified=1,
                identity_verify_status='已通過',
                identity_reject_reason=NULL,
                identity_verified_at=?
            WHERE id=?
            """,
            (
                now_text(),
                verify["user_id"]
            )
        )

        create_notification_realtime(
            verify["user_id"],
            "✅ 你的身分驗證已通過，現在可以安心交易。",
            "/profile/" + str(verify["user_id"])
        )

        flash("已通過身分驗證", "success")

    elif action == "reject":
        if not reject_reason:
            reject_reason = "資料不清楚或不符合平台規範"

        db.execute(
            """
            UPDATE identity_verifications
            SET status='已駁回',
                reject_reason=?,
                reviewed_at=?,
                reviewed_by=?
            WHERE id=?
            """,
            (
                reject_reason,
                now_text(),
                session["user_id"],
                verify_id
            )
        )

        db.execute(
            """
            UPDATE users
            SET identity_verified=0,
                identity_verify_status='已駁回',
                identity_reject_reason=?
            WHERE id=?
            """,
            (
                reject_reason,
                verify["user_id"]
            )
        )

        create_notification_realtime(
            verify["user_id"],
            "❌ 你的身分驗證未通過：" + reject_reason,
            "/identity-verify"
        )

        flash("已駁回身分驗證", "success")

    db.commit()
    db.close()

    return redirect("/admin")
'''

if '@app.route("/identity-verify", methods=["GET", "POST"])' not in text:
    marker = '@app.route("/forgot-password", methods=["GET", "POST"])'
    if marker not in text:
        raise SystemExit("找不到 forgot-password route 插入點")
    text = text.replace(marker, identity_routes + "\n\n" + marker, 1)

identity_admin_query = '''
    identity_requests = db.execute(
        """
        SELECT
            identity_verifications.*,
            users.username,
            users.email
        FROM identity_verifications
        LEFT JOIN users ON identity_verifications.user_id = users.id
        ORDER BY identity_verifications.id DESC
        LIMIT 200
        """
    ).fetchall()
'''

if "identity_requests = db.execute(" not in text:
    marker = "    coin_logs = db.execute("
    if marker not in text:
        raise SystemExit("找不到 admin route 裡 coin_logs 插入點")
    text = text.replace(marker, identity_admin_query + "\n" + marker, 1)

if "identity_requests=identity_requests," not in text:
    text = text.replace(
        "        announcements=announcements,",
        "        announcements=announcements,\n        identity_requests=identity_requests,",
        1
    )

old_identity_badge = '''        "identity_badge": "已驗證" if (
            "email" in user.keys()
            and user["email"]
            and user["email"].endswith("@gm.student.ncut.edu.tw")
        ) else "一般會員"'''

new_identity_badge = '''        "identity_badge": "已身分驗證" if (
            "identity_verified" in user.keys()
            and int(user["identity_verified"] or 0) == 1
        ) else "一般會員"'''

if old_identity_badge in text:
    text = text.replace(old_identity_badge, new_identity_badge, 1)

APP_PATH.write_text(text, encoding="utf-8")

print("已完成 app.py 身分驗證功能修改")
print(f"原檔備份：{backup.name}")
print("接著把 identity_verify.html 放到 templates/，並把 admin_identity_snippet.html 貼到 templates/admin.html。")